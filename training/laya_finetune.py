# Path: training/laya_finetune.py
# Purpose: Local single-GPU LoRA/RLCD fine-tuning adapted from Laya's 2xT4 notebook.

from __future__ import annotations

import json
import math
import os
import random
import shutil
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from training.laya_runtime import Laya4060Config, LayaRuntimeError, patch_peft_forward, preflight


class LayaFineTuneError(RuntimeError):
    """Raised when local Laya fine-tuning cannot start safely."""


def _write_progress(path: Path | None, payload: Mapping[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    record = dict(payload)
    record.setdefault("updated_unix", time.time())
    temporary.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    history_path = path.with_name("progress_history.jsonl")
    with history_path.open("a", encoding="utf-8") as history:
        history.write(json.dumps(record, sort_keys=True) + "\n")


def _checkpoint_interval() -> int:
    """Return the optimizer-step interval for resumable checkpoints."""
    value = os.environ.get("LAYA_CHECKPOINT_INTERVAL", "400").strip()
    try:
        interval = int(value)
    except ValueError as error:
        raise LayaFineTuneError("LAYA_CHECKPOINT_INTERVAL must be a non-negative integer") from error
    if interval < 0:
        raise LayaFineTuneError("LAYA_CHECKPOINT_INTERVAL must be a non-negative integer")
    return interval


def _checkpoint_keep_count() -> int:
    value = os.environ.get("LAYA_CHECKPOINT_KEEP", "3").strip()
    try:
        keep = int(value)
    except ValueError as error:
        raise LayaFineTuneError("LAYA_CHECKPOINT_KEEP must be a positive integer") from error
    if keep < 1:
        raise LayaFineTuneError("LAYA_CHECKPOINT_KEEP must be a positive integer")
    return keep


def _resolve_resume_checkpoint(output_dir: Path) -> Path | None:
    """Resolve an explicit checkpoint or the newest checkpoint in output_dir."""
    requested = os.environ.get("LAYA_RESUME_CHECKPOINT", "").strip()
    if not requested:
        return None
    root = output_dir / "checkpoints"
    if requested.lower() == "latest":
        candidates = [
            path for path in root.glob("step-*")
            if path.is_dir() and (path / "trainer_state.pt").is_file()
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda path: int(path.name.removeprefix("step-")))
    path = Path(requested).expanduser()
    if not path.is_absolute():
        path = output_dir / path
    if not path.is_dir() or not (path / "adapter_model.safetensors").is_file():
        raise LayaFineTuneError(
            f"resume checkpoint is missing adapter_model.safetensors: {path}"
        )
    return path


def _save_training_checkpoint(
    *,
    model,
    optimizer,
    scheduler,
    scaler,
    torch,
    checkpoint_root: Path,
    optimizer_steps: int,
    next_epoch: int,
    next_start: int,
    steps: int,
    total_tokens: int,
    loss_total: float,
    elapsed_seconds: float,
    items_count: int,
    calibration_count: int,
    config: "LayaFineTuneConfig",
    seed: int,
    updates: int,
    keep_count: int,
) -> Path:
    """Atomically persist model, optimizer, scheduler, and RNG state."""
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    final = checkpoint_root / f"step-{optimizer_steps:08d}"
    temporary = checkpoint_root / f".step-{optimizer_steps:08d}-{os.getpid()}-{time.time_ns()}"
    temporary.mkdir(parents=True, exist_ok=False)
    try:
        save_model = getattr(model, "_orig_mod", model)
        save_model.save_pretrained(temporary, safe_serialization=True)
        torch.save(
            {
                "format": "laya_training_checkpoint_v1",
                "optimizer": optimizer.state_dict(),
                "scheduler": scheduler.state_dict(),
                "scaler": scaler.state_dict(),
                "python_rng": random.getstate(),
                "torch_rng": torch.get_rng_state(),
                "cuda_rng": torch.cuda.get_rng_state_all(),
                "optimizer_steps": optimizer_steps,
                "next_epoch": next_epoch,
                "next_start": next_start,
                "steps": steps,
                "total_tokens": total_tokens,
                "loss_total": loss_total,
                "elapsed_seconds": elapsed_seconds,
                "items_count": items_count,
                "calibration_count": calibration_count,
                "seed": seed,
                "updates": updates,
                "profile": config.runtime.profile_id,
                "micro_batch": config.runtime.micro_batch,
                "gradient_accumulation": config.runtime.gradient_accumulation,
                "gradient_checkpointing": config.gradient_checkpointing,
                "torch_compile": config.torch_compile,
                "attention_implementation": config.attention_implementation,
                "fixed_option_width": config.fixed_option_width,
                "fused_adamw": config.fused_adamw,
                "sequence_version": getattr(config, "sequence_version", "factorio-decision-v2"),
            },
            temporary / "trainer_state.pt",
        )
        (temporary / "checkpoint.json").write_text(
            json.dumps(
                {
                    "format": "laya_training_checkpoint_v1",
                    "optimizer_steps": optimizer_steps,
                    "next_epoch": next_epoch,
                    "next_start": next_start,
                    "items_count": items_count,
                    "calibration_count": calibration_count,
                    "profile": config.runtime.profile_id,
                    "updated_unix": time.time(),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if final.exists():
            shutil.rmtree(final)
        temporary.replace(final)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    candidates = sorted(
        (
            path for path in checkpoint_root.glob("step-*")
            if path.is_dir() and (path / "trainer_state.pt").is_file()
        ),
        key=lambda path: int(path.name.removeprefix("step-")),
        reverse=True,
    )
    for old in candidates[keep_count:]:
        shutil.rmtree(old, ignore_errors=True)
    latest = checkpoint_root / "latest"
    latest_tmp = checkpoint_root / f".latest-{os.getpid()}-{time.time_ns()}"
    latest_tmp.write_text(final.name + "\n", encoding="utf-8")
    latest_tmp.replace(latest)
    return final


@dataclass(frozen=True)
class LayaFineTuneConfig:
    runtime: Laya4060Config = Laya4060Config()
    learning_rate_encoder: float = 2.5e-5
    learning_rate_head: float = 1.0e-4
    weight_decay: float = 0.01
    sigma_start: float = 0.4
    sigma_end: float = 0.1
    lora_rank: int = 8
    lora_alpha: int = 16
    lora_dropout: float = 0.05
    gradient_checkpointing: bool = True
    seed: int = 42
    torch_compile: bool = False
    attention_implementation: str = "sdpa"
    fixed_option_width: int | None = None
    fused_adamw: bool = False
    sequence_version: str = "factorio-decision-v2"


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _env_optional_int(name: str) -> int | None:
    value = os.environ.get(name)
    if value is None or not value.strip() or value.strip() == "0":
        return None
    try:
        parsed = int(value)
    except ValueError as error:
        raise LayaFineTuneError(f"{name} must be a positive integer or 0") from error
    if parsed < 1:
        raise LayaFineTuneError(f"{name} must be a positive integer or 0")
    return parsed


def _attention_implementation() -> str:
    value = os.environ.get("LAYA_ATTN_IMPLEMENTATION", os.environ.get("LAYA_ATTENTION_IMPLEMENTATION", "sdpa"))
    value = value.strip().lower()
    if value not in {"sdpa", "flex_attention"}:
        raise LayaFineTuneError("LAYA_ATTN_IMPLEMENTATION must be sdpa or flex_attention")
    return value


def load_cases(path: Path | str, *, split: str = "train") -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as error:
                raise LayaFineTuneError(f"invalid JSONL at line {line_number}") from error
            if case.get("split") == split:
                cases.append(case)
    if not cases:
        raise LayaFineTuneError(f"no {split!r} cases found in {path}")
    return cases


def _require_training_dependencies():
    try:
        import torch
        from laya.common import QTYPES, proper_reward, render_options
        from transformers import AutoTokenizer
    except ImportError as error:
        raise LayaFineTuneError(
            "install torch, laya, transformers, peft, and safetensors before fine-tuning"
        ) from error
    from training.laya_sequence import SEQUENCE_VERSION, build_decision_sequence
    return torch, QTYPES, build_decision_sequence, proper_reward, render_options, AutoTokenizer, SEQUENCE_VERSION


def build_training_items(cases: Iterable[Mapping[str, Any]], tokenizer, *, max_len: int, head_max_len: int) -> list[dict[str, Any]]:
    """Build causal, weighted examples with the shared sequence boundary."""
    _, QTYPES, build_sequence, _, render_options, _, sequence_version = _require_training_dependencies()
    items: list[dict[str, Any]] = []
    for case in cases:
        state = case["state"]
        gold = case["gold"]
        for question_id, question in case["questions"].items():
            if question_id not in gold:
                continue
            qtype = question["type"]
            criteria = question.get("criteria", {})
            gold_question = gold[question_id]
            if qtype == "choice":
                keys = list(criteria)
                target = [float(gold_question.get("probabilities", {}).get(key, 0.0)) for key in keys]
            elif qtype == "noul":
                probabilities = gold_question.get("probabilities", {})
                target = [float(probabilities.get("false", 0.5)), float(probabilities.get("true", 0.5))]
            else:
                levels = len(criteria) if isinstance(criteria, list) else 4
                target = [float(gold_question.get("probabilities", {}).get(str(index), 0.0)) for index in range(levels)]
            if not target or any(value < 0 or not math.isfinite(value) for value in target):
                continue
            total = sum(target)
            # An all-zero target means that no alternative is known. Do not
            # turn that uncertainty into a uniform synthetic preference.
            if total <= 0:
                continue
            target = [value / total for value in target]
            weights = case.get("weights") or {}
            try:
                case_weight = float(weights.get("case_weight", 1.0))
                question_weight = float((weights.get("question_weights") or {}).get(question_id, case_weight))
                temporal_decay = float(weights.get("temporal_decay", 1.0))
            except (TypeError, ValueError) as error:
                raise LayaFineTuneError(f"invalid training weight for {case.get('case_id', '<unknown>')}") from error
            if any(not math.isfinite(value) or value < 0 for value in (case_weight, question_weight, temporal_decay)):
                raise LayaFineTuneError(f"training weights must be finite and non-negative: {case.get('case_id', '<unknown>')}")
            effective_weight = case_weight * question_weight * temporal_decay
            sequence, markers = build_sequence(
                tokenizer,
                state,
                {"type": qtype, "t": qtype, "instructions": question["instructions"], "ins": question["instructions"], "criteria": criteria, "crit": criteria},
                max_len,
                head_max_len,
            )
            if len(markers) != len(render_options({"t": qtype, "crit": criteria})):
                continue
            source_quality = float(weights.get("source_quality", 1.0))
            if not math.isfinite(source_quality) or not 0.0 <= source_quality <= 1.0:
                raise LayaFineTuneError(f"source_quality must be finite in [0,1]: {case.get('case_id', '<unknown>')}")
            items.append({
                "ids": sequence,
                "markers": markers,
                "qtype": QTYPES[qtype],
                "target": target,
                "label": target.index(max(target)),
                "weight": effective_weight * source_quality,
                "case_weight": case_weight,
                "question_weight": question_weight,
                "source_quality": source_quality,
                "case_id": str(case.get("case_id", "")),
                "question_id": str(question_id),
                "sequence_version": sequence_version,
            })
    if not items:
        raise LayaFineTuneError("no model sequences could be built from the dataset")
    return items


def _collate(
    items: list[dict[str, Any]],
    pad_id: int,
    torch,
    *,
    fixed_option_width: int | None = None,
) -> dict[str, Any]:
    size = len(items)
    sequence_length = max(len(item["ids"]) for item in items)
    option_count = fixed_option_width or max(len(item["markers"]) for item in items)
    if any(len(item["markers"]) > option_count for item in items):
        raise LayaFineTuneError(
            f"fixed option width {option_count} is smaller than a batch option set"
        )
    ids = torch.full((size, sequence_length), pad_id, dtype=torch.long)
    attention = torch.zeros((size, sequence_length), dtype=torch.long)
    positions = torch.zeros((size, option_count), dtype=torch.long)
    mask = torch.zeros((size, option_count), dtype=torch.bool)
    target = torch.zeros((size, option_count), dtype=torch.float32)
    weights = torch.zeros((size,), dtype=torch.float32)
    for index, item in enumerate(items):
        ids[index, :len(item["ids"])] = torch.tensor(item["ids"])
        attention[index, :len(item["ids"])] = 1
        count = len(item["markers"])
        positions[index, :count] = torch.tensor(item["markers"])
        mask[index, :count] = True
        target[index, :len(item["target"])] = torch.tensor(item["target"], dtype=torch.float32)
        weights[index] = float(item.get("weight", 1.0))
    return {"input_ids": ids, "attention_mask": attention, "marker_pos": positions, "marker_mask": mask, "target": target, "qtype": torch.tensor([item["qtype"] for item in items]), "weight": weights}


def _weighted_mean(values, weights, torch):
    """Scale each example before averaging, retaining weights at micro-batch 1."""
    if values.ndim != 1 or weights.ndim != 1 or values.shape != weights.shape:
        raise LayaFineTuneError("weighted mean expects equally sized vectors")
    normalized = weights.to(dtype=values.dtype).clamp_min(0.0)
    return (values * normalized).mean()


def fit_temperature(predictions: list[tuple[list[float], list[float]]], torch) -> float:
    if len(predictions) < 10:
        return 1.0
    width = max(len(logits) for logits, _target in predictions)
    logits = torch.full((len(predictions), width), -1e4)
    target = torch.zeros((len(predictions), width))
    for index, (values, probabilities) in enumerate(predictions):
        logits[index, :len(values)] = torch.tensor(values)
        target[index, :len(probabilities)] = torch.tensor(probabilities)
    log_temperature = torch.zeros(1, requires_grad=True)
    optimizer = torch.optim.LBFGS([log_temperature], lr=0.1, max_iter=100)

    def closure():
        optimizer.zero_grad()
        loss = -(target * torch.log_softmax(logits / log_temperature.exp(), -1)).sum(-1).mean()
        loss.backward()
        return loss

    optimizer.step(closure)
    return float(torch.clamp(log_temperature.exp(), 0.1, 10.0).item())


def _lora_targets(model) -> list[str]:
    names = {name.rsplit(".", 1)[-1] for name, module in model.named_modules() if name and hasattr(module, "weight")}
    # Laya's ModernBERT-large encoder uses fused ``Wqkv`` and ``Wo`` names;
    # keep the common split-projection names for compatible checkpoints.
    targets = [name for name in ("q_proj", "k_proj", "v_proj", "o_proj", "Wqkv", "Wo") if name in names]
    if not targets:
        raise LayaFineTuneError("could not identify ModernBERT attention projection modules for LoRA")
    return targets


def train_local(
    cases_path: Path | str,
    model_dir: Path | str,
    output_dir: Path | str,
    *,
    config: LayaFineTuneConfig | None = None,
) -> dict[str, Any]:
    """Run the upstream RLCD objective on one local GPU with LoRA adapters."""
    if config is None:
        micro_batch = int(os.environ.get("LAYA_MICRO_BATCH", "1"))
        gradient_accumulation = int(os.environ.get("LAYA_GRADIENT_ACCUMULATION", "32"))
        gradient_checkpointing = _env_bool("LAYA_GRADIENT_CHECKPOINTING", True)
        torch_compile = _env_bool("LAYA_TORCH_COMPILE", False)
        attention_implementation = _attention_implementation()
        fixed_option_width = _env_optional_int("LAYA_FIXED_OPTION_WIDTH")
        fused_adamw = _env_bool("LAYA_FUSED_ADAMW", False)
        config = LayaFineTuneConfig(
            runtime=Laya4060Config(
                micro_batch=micro_batch,
                gradient_accumulation=gradient_accumulation,
                gradient_checkpointing=gradient_checkpointing,
                torch_compile=torch_compile,
                attention_implementation=attention_implementation,
                fixed_option_width=fixed_option_width,
                fused_adamw=fused_adamw,
            ),
            gradient_checkpointing=gradient_checkpointing,
            torch_compile=torch_compile,
            attention_implementation=attention_implementation,
            fixed_option_width=fixed_option_width,
            fused_adamw=fused_adamw,
        )
    if config.torch_compile and config.fixed_option_width is None:
        raise LayaFineTuneError(
            "LAYA_TORCH_COMPILE=1 requires LAYA_FIXED_OPTION_WIDTH for a static option shape"
        )
    cases_path = Path(cases_path)
    model_dir = Path(model_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    progress_path = Path(os.environ["LAYA_PROGRESS_PATH"]) if os.environ.get("LAYA_PROGRESS_PATH") else None
    checkpoint_interval = _checkpoint_interval()
    checkpoint_keep = _checkpoint_keep_count()
    checkpoint_root = output_dir / "checkpoints"
    resume_checkpoint = _resolve_resume_checkpoint(output_dir)
    if not cases_path.is_file():
        raise LayaFineTuneError(f"dataset file does not exist: {cases_path}")
    if not model_dir.is_dir():
        raise LayaFineTuneError(f"Laya model directory does not exist: {model_dir}")
    if not (model_dir / "rl_agent_config.json").is_file():
        raise LayaFineTuneError(
            f"Laya model directory is missing rl_agent_config.json: {model_dir}"
        )
    try:
        report = preflight(config.runtime)
    except LayaRuntimeError as error:
        raise LayaFineTuneError(str(error)) from error
    if report["status"] != "ready":
        raise LayaFineTuneError(f"Laya GPU preflight blocked: {report.get('reason')}")
    torch, QTYPES, build_sequence, proper_reward, render_options, AutoTokenizer, sequence_version = _require_training_dependencies()
    if config.sequence_version != sequence_version:
        config = replace(config, sequence_version=sequence_version)
    try:
        from laya.agent import _fix_tokenizer_config
        from laya.common import build_model
        from peft import LoraConfig, TaskType, get_peft_model, set_peft_model_state_dict
        from safetensors.torch import load_file
    except ImportError as error:
        raise LayaFineTuneError("the installed Laya stack lacks model/LoRA training dependencies") from error

    random.seed(config.seed)
    torch.manual_seed(config.seed)
    with (model_dir / "rl_agent_config.json").open(encoding="utf-8") as handle:
        model_config = json.load(handle)
    model_config.update({
        "gradient_checkpointing": config.gradient_checkpointing,
        "max_len": config.runtime.max_len,
        "head_max_len": config.runtime.head_max_len,
        "sequence_version": sequence_version,
    })
    _fix_tokenizer_config(str(model_dir))
    tokenizer = AutoTokenizer.from_pretrained(model_dir / "tokenizer")
    cases = load_cases(cases_path, split="train")
    calibration_cases = load_cases(cases_path, split="calibration")
    items = build_training_items(cases, tokenizer, max_len=config.runtime.max_len, head_max_len=config.runtime.head_max_len)
    calibration_items = build_training_items(calibration_cases, tokenizer, max_len=config.runtime.max_len, head_max_len=config.runtime.head_max_len)
    model = build_model(model_config, encoder_dir=model_dir / "encoder")
    model.load_state_dict(load_file(str(model_dir / "model.safetensors")), strict=True)
    targets = _lora_targets(model)
    # The domain-specific decision head includes the transformer head, option
    # scorer, question-type embedding, and escalation head.  Keep all of it
    # trainable; only the ModernBERT encoder is adapted through LoRA.
    decision_modules = [
        name for name in ("head", "type_emb", "scorer", "act_head")
        if any(parameter_name == name or parameter_name.startswith(name + ".") for parameter_name, _ in model.named_parameters())
    ]
    modules_to_save = decision_modules or None
    lora = LoraConfig(task_type=TaskType.FEATURE_EXTRACTION, r=config.lora_rank, lora_alpha=config.lora_alpha, lora_dropout=config.lora_dropout, target_modules=targets, modules_to_save=modules_to_save)
    patch_peft_forward(model)
    model = get_peft_model(model, lora)
    resume_state = None
    warm_start_checkpoint = False
    if resume_checkpoint is not None:
        trainer_state_path = resume_checkpoint / "trainer_state.pt"
        if trainer_state_path.is_file():
            resume_state = torch.load(
                trainer_state_path,
                map_location="cpu",
                weights_only=False,
            )
            expected = {
                "items_count": len(items),
                "calibration_count": len(calibration_items),
                "profile": config.runtime.profile_id,
                "micro_batch": config.runtime.micro_batch,
                "gradient_accumulation": config.runtime.gradient_accumulation,
                "fixed_option_width": config.fixed_option_width,
                "sequence_version": sequence_version,
            }
            mismatches = {
                key: (resume_state.get(key), value)
                for key, value in expected.items()
                if resume_state.get(key) != value
            }
            if mismatches:
                raise LayaFineTuneError(
                    f"resume checkpoint is incompatible with the selected run: {mismatches}"
                )
        else:
            # A completed PEFT adapter can be used as a deliberate warm start,
            # but it has no optimizer/RNG state and is therefore not an exact
            # resume.  The new output directory immediately gets a step-zero
            # recovery snapshot after loading it.
            warm_start_checkpoint = True
        adapter_path = resume_checkpoint / "adapter_model.safetensors"
        if not adapter_path.is_file():
            raise LayaFineTuneError(f"resume checkpoint is missing adapter weights: {resume_checkpoint}")
        adapter_state = load_file(str(adapter_path))
        try:
            load_result = set_peft_model_state_dict(model, adapter_state)
        except (KeyError, RuntimeError, ValueError) as error:
            raise LayaFineTuneError(
                f"resume checkpoint adapter does not match the model: {resume_checkpoint}"
            ) from error
        if getattr(load_result, "unexpected_keys", ()):
            raise LayaFineTuneError(
                f"resume checkpoint adapter has unexpected keys: {resume_checkpoint}"
            )
    if config.gradient_checkpointing:
        model.encoder.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    else:
        model.encoder.gradient_checkpointing_disable()
    torch.set_float32_matmul_precision("high")
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    model.to("cuda")
    model.train()
    # Flex attention and compilation are opt-in training accelerators.  The
    # predictor/evaluator intentionally remain eager SDPA for checkpoint
    # compatibility and deterministic deployment behavior.
    model.encoder.config._attn_implementation = config.attention_implementation
    encoder_parameters = [parameter for name, parameter in model.named_parameters() if "lora_" in name]
    head_parameters = [parameter for name, parameter in model.named_parameters() if "lora_" not in name and parameter.requires_grad]
    optimizer_kwargs: dict[str, Any] = {}
    if config.fused_adamw:
        optimizer_kwargs["fused"] = True
    optimizer = torch.optim.AdamW([
        {"params": encoder_parameters, "lr": config.learning_rate_encoder},
        {"params": head_parameters, "lr": config.learning_rate_head},
    ], weight_decay=config.weight_decay, **optimizer_kwargs)
    if config.torch_compile:
        compile_fn = getattr(torch, "compile", None)
        if compile_fn is None:
            raise LayaFineTuneError("LAYA_TORCH_COMPILE=1 requires torch.compile")
        try:
            model = compile_fn(model, mode="default", dynamic=False)
        except Exception as error:  # pragma: no cover - depends on local CUDA/Inductor
            raise LayaFineTuneError(f"torch.compile could not build the selected profile: {error}") from error
    scaler = torch.amp.GradScaler("cuda", enabled=report.get("bf16_supported") is not True)
    updates = max(1, math.ceil(len(items) / config.runtime.micro_batch / config.runtime.gradient_accumulation) * config.runtime.epochs)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=updates, eta_min=1e-6)
    if resume_state is not None:
        if resume_state.get("updates") != updates:
            raise LayaFineTuneError(
                f"resume checkpoint has {resume_state.get('updates')} updates; current run requires {updates}"
            )
        optimizer.load_state_dict(resume_state["optimizer"])
        scheduler.load_state_dict(resume_state["scheduler"])
        scaler.load_state_dict(resume_state.get("scaler", {}))
        random.setstate(resume_state["python_rng"])
        torch.set_rng_state(resume_state["torch_rng"])
        torch.cuda.set_rng_state_all(resume_state["cuda_rng"])
        loss_total = float(resume_state.get("loss_total", 0.0))
        steps = int(resume_state.get("steps", 0))
        total_tokens = int(resume_state.get("total_tokens", 0))
        resume_epoch = int(resume_state.get("next_epoch", 0))
        resume_start = int(resume_state.get("next_start", 0))
        train_started = time.monotonic() - float(resume_state.get("elapsed_seconds", 0.0))
    else:
        loss_total = 0.0
        steps = 0
        total_tokens = 0
        resume_epoch = 0
        resume_start = 0
        train_started = time.monotonic()
    _write_progress(progress_path, {
        "status": "resuming" if resume_state is not None else ("warm_start" if warm_start_checkpoint else "training"),
        "epoch": resume_epoch, "epochs": config.runtime.epochs,
        "steps": steps, "estimated_steps": updates, "cases": len(items),
        "optimizer_steps": math.ceil(steps / config.runtime.gradient_accumulation),
        "resume_checkpoint": str(resume_checkpoint) if resume_checkpoint is not None else None,
        "micro_batch": config.runtime.micro_batch,
        "gradient_accumulation": config.runtime.gradient_accumulation,
        "gradient_checkpointing": config.gradient_checkpointing,
        "torch_compile": config.torch_compile,
        "attention_implementation": config.attention_implementation,
        "fixed_option_width": config.fixed_option_width,
        "fused_adamw": config.fused_adamw,
    })
    if resume_state is None and checkpoint_interval:
        _save_training_checkpoint(
            model=model,
            optimizer=optimizer,
            scheduler=scheduler,
            scaler=scaler,
            torch=torch,
            checkpoint_root=checkpoint_root,
            optimizer_steps=0,
            next_epoch=0,
            next_start=0,
            steps=0,
            total_tokens=0,
            loss_total=0.0,
            elapsed_seconds=0.0,
            items_count=len(items),
            calibration_count=len(calibration_items),
            config=config,
            seed=config.seed,
            updates=updates,
            keep_count=checkpoint_keep,
        )
    for epoch in range(resume_epoch, config.runtime.epochs):
        random.Random(config.seed + epoch).shuffle(items)
        sigma = config.sigma_start + (config.sigma_end - config.sigma_start) * epoch / max(1, config.runtime.epochs - 1)
        optimizer.zero_grad(set_to_none=True)
        first_start = resume_start if epoch == resume_epoch else 0
        for start in range(first_start, len(items), config.runtime.micro_batch):
            batch = _collate(
                items[start:start + config.runtime.micro_batch],
                tokenizer.pad_token_id,
                torch,
                fixed_option_width=config.fixed_option_width,
            )
            with torch.autocast("cuda", dtype=torch.bfloat16 if report.get("bf16_supported") else torch.float16):
                logits, act = model(
                    input_ids=batch["input_ids"].cuda(),
                    attention_mask=batch["attention_mask"].cuda(),
                    marker_pos=batch["marker_pos"].cuda(),
                    marker_mask=batch["marker_mask"].cuda(),
                    qtype=batch["qtype"].cuda(),
                )
            logits = logits.float()
            mask = batch["marker_mask"].cuda()
            width = mask.sum(-1, keepdim=True).float()
            target = batch["target"].cuda()
            example_weight = batch["weight"].cuda().clamp_min(0.0)
            probabilities = torch.softmax(logits.masked_fill(~mask, -1e4), -1)
            entropy = float((-(probabilities * torch.log(probabilities.clamp_min(1e-9))).sum(-1) / torch.log(width.clamp_min(2.0))).mean().detach().cpu())
            noise = torch.randn((config.runtime.group_size,) + logits.shape, device="cuda") * sigma * mask
            noise = (noise - noise.sum(-1, keepdim=True) / width) * mask
            sampled = logits.detach().unsqueeze(0) + noise
            with torch.no_grad():
                reward = proper_reward(torch.softmax(sampled.masked_fill(~mask, -1e4), -1), target.unsqueeze(0), batch["qtype"].cuda(), mask, w_sph=0.75, w_rps=1.0)
                advantage = (reward - reward.mean(0, keepdim=True)) / (reward.std() + 1e-6)
            log_probability = -(((sampled - logits.unsqueeze(0)) ** 2) * mask).sum(-1) / (2 * sigma ** 2)
            weighted_policy = (advantage * log_probability).mean(0)
            weighted_supervised = (target * torch.log_softmax(logits.masked_fill(~mask, -1e4), -1)).sum(-1)
            objective_loss = -_weighted_mean(weighted_policy, example_weight, torch) - _weighted_mean(weighted_supervised, example_weight, torch)
            objective_loss = objective_loss + 0.0 * act.sum()
            loss = objective_loss / config.runtime.gradient_accumulation
            scaler.scale(loss).backward()
            steps += 1
            total_tokens += int(batch["attention_mask"].sum())
            loss_total += float(objective_loss.detach().cpu())
            if steps % config.runtime.gradient_accumulation == 0 or start + config.runtime.micro_batch >= len(items):
                scaler.unscale_(optimizer)
                grad_norm = float(torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0).detach().cpu())
                scaler.step(optimizer)
                scaler.update()
                scheduler.step()
                optimizer.zero_grad(set_to_none=True)
                elapsed = max(1e-6, time.monotonic() - train_started)
                optimizer_steps = math.ceil(steps / config.runtime.gradient_accumulation)
                update_rate = optimizer_steps / elapsed
                _write_progress(progress_path, {
                    "status": "training", "epoch": epoch + 1, "epochs": config.runtime.epochs,
                    "steps": steps, "estimated_steps": updates, "cases": len(items),
                    "optimizer_steps": optimizer_steps,
                    "loss": float(objective_loss.detach().cpu()),
                    "mean_loss": loss_total / max(1, steps),
                    "grad_norm": grad_norm,
                    "entropy": entropy,
                    "reward_mean": float(reward.mean().detach().cpu()),
                    "reward_std": float(reward.std().detach().cpu()),
                    "learning_rate_encoder": optimizer.param_groups[0]["lr"],
                    "learning_rate_head": optimizer.param_groups[-1]["lr"],
                    "tokens": total_tokens,
                    "tokens_per_second": total_tokens / elapsed,
                    "microbatches_per_second": steps / elapsed,
                    "examples_per_second": (steps * config.runtime.micro_batch) / elapsed,
                    "elapsed_seconds": elapsed,
                    "eta_seconds": max(0.0, (updates - optimizer_steps) / max(update_rate, 1e-9)),
                    "peak_vram_bytes": int(torch.cuda.max_memory_allocated()),
                    "micro_batch": config.runtime.micro_batch,
                    "gradient_accumulation": config.runtime.gradient_accumulation,
                    "gradient_checkpointing": config.gradient_checkpointing,
                    "torch_compile": config.torch_compile,
                    "attention_implementation": config.attention_implementation,
                    "fixed_option_width": config.fixed_option_width,
                    "fused_adamw": config.fused_adamw,
                })
                next_start = start + config.runtime.micro_batch
                next_epoch = epoch
                if next_start >= len(items):
                    next_epoch += 1
                    next_start = 0
                if checkpoint_interval and (
                    optimizer_steps % checkpoint_interval == 0 or next_epoch > epoch
                ):
                    _save_training_checkpoint(
                        model=model,
                        optimizer=optimizer,
                        scheduler=scheduler,
                        scaler=scaler,
                        torch=torch,
                        checkpoint_root=checkpoint_root,
                        optimizer_steps=optimizer_steps,
                        next_epoch=next_epoch,
                        next_start=next_start,
                        steps=steps,
                        total_tokens=total_tokens,
                        loss_total=loss_total,
                        elapsed_seconds=elapsed,
                        items_count=len(items),
                        calibration_count=len(calibration_items),
                        config=config,
                        seed=config.seed,
                        updates=updates,
                        keep_count=checkpoint_keep,
                    )
        resume_start = 0
    # Fit temperatures on the independent calibration split, never on the
    # training items. This is the calibration step from the upstream notebook
    # with the split discipline required by the Factorio dataset contract.
    model.eval()
    calibration_predictions: dict[int, list[tuple[list[float], list[float]]]] = {0: [], 1: [], 2: []}
    with torch.no_grad():
        for start in range(0, len(calibration_items), config.runtime.micro_batch):
            batch = _collate(
                calibration_items[start:start + config.runtime.micro_batch],
                tokenizer.pad_token_id,
                torch,
                fixed_option_width=config.fixed_option_width,
            )
            with torch.autocast("cuda", dtype=torch.bfloat16 if report.get("bf16_supported") else torch.float16):
                logits, _ = model(
                    input_ids=batch["input_ids"].cuda(),
                    attention_mask=batch["attention_mask"].cuda(),
                    marker_pos=batch["marker_pos"].cuda(),
                    marker_mask=batch["marker_mask"].cuda(),
                    qtype=batch["qtype"].cuda(),
                )
            for index, item in enumerate(calibration_items[start:start + config.runtime.micro_batch]):
                calibration_predictions[item["qtype"]].append((logits[index, :len(item["markers"])].float().cpu().tolist(), item["target"]))
    temperatures = [fit_temperature(calibration_predictions[index], torch) for index in range(3)]
    output_dir.mkdir(parents=True, exist_ok=True)
    save_model = getattr(model, "_orig_mod", model)
    save_model.save_pretrained(output_dir)
    tokenizer.save_pretrained(output_dir / "tokenizer")
    model_config.update({
        "fine_tuned": True,
        "profile": config.runtime.profile_id,
        "checkpoint_format": "peft_lora",
        "base_model_dir": str(model_dir.resolve()),
        "lora_targets": targets,
        "decision_modules": decision_modules,
        "lora_rank": config.lora_rank,
        "lora_alpha": config.lora_alpha,
        "steps": steps,
        "calibration_cases": len(calibration_items),
        "temperature": temperatures,
        "micro_batch": config.runtime.micro_batch,
        "gradient_accumulation": config.runtime.gradient_accumulation,
        "gradient_checkpointing": config.gradient_checkpointing,
        "torch_compile": config.torch_compile,
        "attention_implementation": config.attention_implementation,
        "fixed_option_width": config.fixed_option_width,
        "fused_adamw": config.fused_adamw,
        "sequence_version": sequence_version,
    })
    (output_dir / "rl_agent_config.json").write_text(json.dumps(model_config, indent=2) + "\n", encoding="utf-8")
    _write_progress(progress_path, {
        "status": "trained", "epoch": config.runtime.epochs, "epochs": config.runtime.epochs,
        "steps": steps, "estimated_steps": updates, "cases": len(items),
        "calibration_cases": len(calibration_items), "mean_loss": loss_total / max(1, steps),
        "micro_batch": config.runtime.micro_batch,
        "gradient_accumulation": config.runtime.gradient_accumulation,
        "gradient_checkpointing": config.gradient_checkpointing,
        "torch_compile": config.torch_compile,
        "attention_implementation": config.attention_implementation,
        "fixed_option_width": config.fixed_option_width,
        "fused_adamw": config.fused_adamw,
    })
    return {
        "profile": config.runtime.profile_id,
        "cases": len(items),
        "calibration_cases": len(calibration_items),
        "steps": steps,
        "mean_loss": loss_total / max(1, steps),
        "temperatures": temperatures,
        "output_dir": str(output_dir),
        "lora_targets": targets,
        "micro_batch": config.runtime.micro_batch,
        "gradient_accumulation": config.runtime.gradient_accumulation,
        "gradient_checkpointing": config.gradient_checkpointing,
        "torch_compile": config.torch_compile,
        "attention_implementation": config.attention_implementation,
        "fixed_option_width": config.fixed_option_width,
        "fused_adamw": config.fused_adamw,
    }


__all__ = ["LayaFineTuneConfig", "LayaFineTuneError", "build_training_items", "fit_temperature", "load_cases", "train_local"]
