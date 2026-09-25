# Path: training/laya_runtime.py
# Purpose: Describe and preflight the single-GPU RTX 4060 Laya fine-tuning profile.

from __future__ import annotations

import importlib
import json
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from training.laya_sequence import (
    LEGACY_SEQUENCE_VERSION,
    SEQUENCE_VERSION,
    LayaSequenceError,
    build_decision_sequence,
    render_decision_options,
)


class LayaRuntimeError(RuntimeError):
    """Raised when the local training device cannot satisfy the profile."""


def resolve_sequence_version(
    config: dict[str, Any], *, allow_legacy_sequence: bool = False,
) -> str:
    """Require an explicit prompt representation before loading a checkpoint."""
    version = config.get("sequence_version")
    if version == SEQUENCE_VERSION:
        return SEQUENCE_VERSION
    checkpoint_format = config.get("checkpoint_format")
    is_trained_checkpoint = bool(
        config.get("fine_tuned")
        or checkpoint_format
        or config.get("training_checkpoint")
    )
    if version is None and not is_trained_checkpoint:
        # Upstream base weights are initialization, not a promoted adapter.
        return LEGACY_SEQUENCE_VERSION
    if version in {None, LEGACY_SEQUENCE_VERSION} and allow_legacy_sequence:
        return LEGACY_SEQUENCE_VERSION
    if version in {None, LEGACY_SEQUENCE_VERSION}:
        raise LayaRuntimeError(
            "checkpoint uses the legacy truncating Laya sequence; retrain with "
            f"sequence_version={SEQUENCE_VERSION!r}, or opt into legacy diagnostic "
            "inference explicitly"
        )
    raise LayaRuntimeError(
        f"unsupported Laya sequence_version {version!r}; expected {SEQUENCE_VERSION!r}"
    )


def patch_peft_forward(model: Any) -> Any:
    """Adapt Laya's custom forward signature to PEFT's HF-style wrapper.

    PEFT always supplies an ``inputs_embeds`` keyword, while Laya's decision
    model consumes marker positions and question types instead.  The adapter
    is intentionally explicit so no tensor is silently reinterpreted.
    """
    original = model.forward

    def forward(
        self,
        input_ids=None,
        attention_mask=None,
        marker_pos=None,
        marker_mask=None,
        qtype=None,
        inputs_embeds=None,
        **kwargs,
    ):
        del kwargs
        if inputs_embeds is not None and input_ids is None:
            raise LayaRuntimeError("Laya decision model requires input_ids, not inputs_embeds")
        return original(input_ids, attention_mask, marker_pos, marker_mask, qtype)

    model.forward = types.MethodType(forward, model)
    return model


class LayaModelPredictor:
    """Load a local Laya checkpoint and expose the policy ``predict`` contract.

    Fine-tuning writes a PEFT adapter while retaining the immutable base model.
    The predictor deliberately accepts only a bounded typed-question request;
    it never turns model text into a Factorio command.
    """

    def __init__(
        self,
        checkpoint_dir: str | Path,
        *,
        base_model_dir: str | Path | None = None,
        device: str | None = None,
        allow_legacy_sequence: bool = False,
        explicit_sequence_version: str | None = None,
    ) -> None:
        # Exploration only: render the current representation for an upstream
        # checkpoint that predates it, instead of legacy truncation.
        if explicit_sequence_version not in {None, SEQUENCE_VERSION}:
            raise LayaRuntimeError(
                f"explicit sequence version must be {SEQUENCE_VERSION!r}"
            )
        checkpoint = Path(checkpoint_dir)
        if not checkpoint.is_dir():
            raise LayaRuntimeError(f"Laya checkpoint directory does not exist: {checkpoint}")
        config_path = checkpoint / "rl_agent_config.json"
        if not config_path.is_file() and base_model_dir is not None:
            config_path = Path(base_model_dir) / "rl_agent_config.json"
        recorded_base: str | None = None
        if config_path.is_file():
            try:
                recorded_base = json.loads(config_path.read_text(encoding="utf-8")).get("base_model_dir")
            except (OSError, json.JSONDecodeError):
                recorded_base = None
        base = Path(base_model_dir or recorded_base or checkpoint)
        if not base.is_dir():
            raise LayaRuntimeError(f"Laya base model directory does not exist: {base}")
        try:
            import numpy as np
            import torch
            from laya.common import (
                QTYPES,
                amp_dtype,
                build_model,
                build_sequence,
                collate_items,
                confidence_from_probs,
                render_options,
                temp_bucket,
            )
            from safetensors.torch import load_file
            from transformers import AutoTokenizer
        except ImportError as error:
            raise LayaRuntimeError(
                "install torch, laya, transformers, peft, and safetensors before inference"
            ) from error

        if not config_path.is_file():
            config_path = base / "rl_agent_config.json"
        if not config_path.is_file():
            raise LayaRuntimeError(f"Laya config is missing from {checkpoint} and {base}")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        base_config_path = base / "rl_agent_config.json"
        if base_config_path.is_file() and checkpoint != base:
            base_config = json.loads(base_config_path.read_text(encoding="utf-8"))
            base_config.update({key: value for key, value in config.items() if key not in {"training"}})
            config = base_config

        self._torch = torch
        self._np = np
        self._QTYPES = QTYPES
        self._amp_dtype = amp_dtype
        self._legacy_build_sequence = build_sequence
        self._collate_items = collate_items
        self._confidence_from_probs = confidence_from_probs
        self._render_options = render_options
        self._temp_bucket = temp_bucket
        self.config = config
        self.sequence_version = explicit_sequence_version or resolve_sequence_version(
            config, allow_legacy_sequence=allow_legacy_sequence,
        )
        self.checkpoint_dir = checkpoint
        self.base_model_dir = base
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))

        tokenizer_dir = checkpoint / "tokenizer"
        if not tokenizer_dir.is_dir():
            tokenizer_dir = base / "tokenizer"
        encoder_dir = base / "encoder"
        tokenizer = AutoTokenizer.from_pretrained(tokenizer_dir)
        model = build_model(config, encoder_dir=str(encoder_dir))
        base_weights = base / "model.safetensors"
        checkpoint_weights = checkpoint / "model.safetensors"
        if checkpoint_weights.is_file():
            model.load_state_dict(load_file(str(checkpoint_weights)), strict=True)
        elif (checkpoint / "adapter_config.json").is_file():
            if not base_weights.is_file():
                raise LayaRuntimeError(f"base Laya weights are missing: {base_weights}")
            model.load_state_dict(load_file(str(base_weights)), strict=True)
            patch_peft_forward(model)
            try:
                from peft import PeftModel
            except ImportError as error:
                raise LayaRuntimeError("PEFT is required to load a Laya adapter checkpoint") from error
            model = PeftModel.from_pretrained(model, checkpoint)
        else:
            raise LayaRuntimeError(
                f"checkpoint has neither model.safetensors nor adapter_config.json: {checkpoint}"
            )
        self.tokenizer = tokenizer
        self.model = model.to(self.device).eval()
        patch_peft_forward(self.model.base_model.model if hasattr(self.model, "base_model") and hasattr(self.model.base_model, "model") else self.model)
        self.model.encoder.config.reference_compile = False
        self.temperature = config.get("temperature", [1.0, 1.0, 1.0])
        self.temperature_by_options = config.get("temperature_by_options", {})
        self.dtype = amp_dtype(config.get("amp_dtype", "fp16"))
        if self.device.type == "cuda" and torch.cuda.get_device_capability(self.device)[0] < 8:
            self.dtype = torch.float16

    @staticmethod
    def _question(qdef: dict[str, Any]) -> dict[str, Any]:
        qtype = qdef["type"]
        criteria = qdef.get("criteria")
        if qtype == "choice" and isinstance(criteria, list):
            criteria = {item: None for item in criteria}
        instructions = qdef.get("instructions", "")
        if not isinstance(instructions, str):
            instructions = json.dumps(instructions, sort_keys=True)
        return {"t": qtype, "ins": instructions, "crit": criteria}

    def predict(self, state: Any, questions: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Run bounded typed questions and return the Laya/Jev answer shape."""
        if not questions:
            raise LayaRuntimeError("Laya inference received no questions")
        items: list[dict[str, Any]] = []
        question_ids = list(questions)
        for question_id in question_ids:
            question = self._question(questions[question_id])
            try:
                if self.sequence_version == SEQUENCE_VERSION:
                    sequence, markers = build_decision_sequence(
                        self.tokenizer,
                        state,
                        question,
                        int(self.config.get("max_len", 1024)),
                        int(self.config.get("head_max_len", 256)),
                    )
                    option_count = len(render_decision_options(question))
                else:
                    sequence, markers = self._legacy_build_sequence(
                        self.tokenizer,
                        state,
                        question,
                        int(self.config.get("max_len", 1024)),
                        int(self.config.get("head_max_len", 256)),
                    )
                    option_count = len(self._render_options(question))
            except LayaSequenceError as error:
                raise LayaRuntimeError(
                    f"question {question_id!r} cannot be encoded losslessly: {error}"
                ) from error
            if len(markers) != option_count:
                raise LayaRuntimeError(
                    f"question {question_id!r} does not fit the configured Laya option head"
                )
            items.append({
                "ids": sequence,
                "markers": markers,
                "qtype": self._QTYPES[question["t"]],
                "target": [0.0] * len(markers),
                "label": -1,
                "episode": 0,
                "ep_step": 0,
                "ep_len": 1,
                "src": "factorio-laya-runtime",
            })
        batch = self._collate_items([items], self.tokenizer.pad_token_id)
        torch = self._torch
        np = self._np
        with torch.no_grad():
            with torch.autocast(
                device_type=self.device.type,
                dtype=self.dtype,
                enabled=self.device.type == "cuda",
            ):
                logits, act = self.model(
                    input_ids=batch["input_ids"].to(self.device),
                    attention_mask=batch["attention_mask"].to(self.device),
                    marker_pos=batch["marker_pos"].to(self.device),
                    marker_mask=batch["marker_mask"].to(self.device),
                    qtype=batch["qtype"].to(self.device),
                )
        logits = logits.float().cpu().numpy()
        act = torch.softmax(act.float(), -1).cpu().numpy()
        answers: dict[str, dict[str, Any]] = {}
        input_tokens = int(batch["attention_mask"].sum())
        for row, question_id in enumerate(question_ids):
            question = self._question(questions[question_id])
            count = len(items[row]["markers"])
            qtype = self._QTYPES[question["t"]]
            temperature = self.temperature_by_options.get(
                self._temp_bucket(qtype, count),
                self.temperature[qtype] if qtype < len(self.temperature) else 1.0,
            )
            values = logits[row, :count] / max(float(temperature), 1e-6)
            probabilities = np.exp(values - values.max())
            probabilities = probabilities / probabilities.sum()
            answer: dict[str, Any] = {
                "confidence": round(float(self._confidence_from_probs(probabilities, count)), 4),
                "probabilities": {
                    str(index): round(float(value), 4)
                    for index, value in enumerate(probabilities)
                },
                "rl_agent": {"act_probability": float(act[row, 0])},
            }
            if question["t"] == "choice":
                keys = list((question.get("crit") or {}).keys())
                answer.update({
                    "type": "choice",
                    "choice": keys[int(probabilities.argmax())],
                    "probabilities": {
                        str(key): round(float(value), 4)
                        for key, value in zip(keys, probabilities)
                    },
                })
            elif question["t"] == "score":
                answer.update({
                    "type": "score",
                    "score": round(float((np.arange(count) * probabilities).sum()), 4),
                    "legend": {str(index): value for index, value in enumerate(question["crit"])},
                })
            else:
                answer.update({"type": "noul", "noul": round(float(probabilities[1]), 4)})
            answers[question_id] = answer
        return {
            "model": "laya-local",
            "answers": answers,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": 0,
                "sequence_version": self.sequence_version,
            },
        }

    def system_one(self, state: Any, questions: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Compatibility alias for Laya's Jev-style inference API."""
        return self.predict(state, questions)


@dataclass(frozen=True)
class Laya4060Config:
    profile_id: str = "laya-4060-lora"
    max_len: int = 1024
    head_max_len: int = 256
    micro_batch: int = 1
    gradient_accumulation: int = 32
    group_size: int = 4
    epochs: int = 4
    min_free_ratio: float = 0.10
    gradient_checkpointing: bool = True
    torch_compile: bool = False
    attention_implementation: str = "sdpa"
    fixed_option_width: int | None = None
    fused_adamw: bool = False

    def __post_init__(self) -> None:
        if self.max_len < 512 or self.head_max_len < 128:
            raise ValueError("Laya context limits are below the supported smoke-test floor")
        if self.micro_batch < 1 or self.gradient_accumulation < 1 or self.group_size < 1 or self.epochs < 1:
            raise ValueError("Laya training batch settings must be positive")
        if not 0.0 < self.min_free_ratio < 1.0:
            raise ValueError("min_free_ratio must be in (0, 1)")
        if self.attention_implementation not in {"sdpa", "flex_attention"}:
            raise ValueError("attention_implementation must be sdpa or flex_attention")
        if self.fixed_option_width is not None and self.fixed_option_width < 1:
            raise ValueError("fixed_option_width must be positive when provided")


def _torch(torch_module: Any | None = None) -> Any:
    if torch_module is not None:
        return torch_module
    try:
        return importlib.import_module("torch")
    except ImportError as error:
        raise LayaRuntimeError("PyTorch is not installed; install the local Laya training dependencies") from error


def preflight(config: Laya4060Config | None = None, *, torch_module: Any | None = None) -> dict[str, Any]:
    """Read device readiness without allocating the Laya model."""
    config = config or Laya4060Config()
    torch = _torch(torch_module)
    profile = {
        "profile": config.profile_id,
        "max_len": config.max_len,
        "head_max_len": config.head_max_len,
        "micro_batch": config.micro_batch,
        "gradient_accumulation": config.gradient_accumulation,
        "group_size": config.group_size,
        "epochs": config.epochs,
        "gradient_checkpointing": config.gradient_checkpointing,
        "torch_compile": config.torch_compile,
        "attention_implementation": config.attention_implementation,
        "fixed_option_width": config.fixed_option_width,
        "fused_adamw": config.fused_adamw,
    }
    if not bool(torch.cuda.is_available()):
        return {**profile, "status": "blocked", "reason": "cuda_unavailable"}
    device = torch.cuda.current_device()
    props = torch.cuda.get_device_properties(device)
    total = int(getattr(props, "total_memory", 0))
    free, observed_total = torch.cuda.mem_get_info(device)
    total = int(observed_total or total)
    free = int(free)
    if total <= 0:
        raise LayaRuntimeError("CUDA reported no device memory")
    report = {
        **profile,
        "status": "ready" if free / total >= config.min_free_ratio else "blocked",
        "device": str(getattr(props, "name", "unknown")),
        "device_index": int(device),
        "total_vram_bytes": total,
        "free_vram_bytes": free,
        "free_vram_ratio": free / total,
        "bf16_supported": bool(getattr(torch.cuda, "is_bf16_supported", lambda: False)()),
    }
    if report["status"] != "ready":
        report["reason"] = "insufficient_free_vram"
    return report


def autocast_dtype(torch_module: Any | None = None) -> str:
    """Choose bf16 where supported; otherwise use fp16."""
    torch = _torch(torch_module)
    if bool(torch.cuda.is_available()) and bool(getattr(torch.cuda, "is_bf16_supported", lambda: False)()):
        return "bfloat16"
    return "float16"


__all__ = [
    "Laya4060Config",
    "LayaModelPredictor",
    "LayaRuntimeError",
    "autocast_dtype",
    "patch_peft_forward",
    "preflight",
    "resolve_sequence_version",
]
