# Path: tools/laya_optimization_bench.py
# Purpose: Run one isolated, short Laya GPU optimization pilot and emit JSON.

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _gpu_sample() -> dict[str, float]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=temperature.gpu,clocks.sm,power.draw",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        temp, clock, power = (float(value.strip()) for value in result.stdout.split(",")[:3])
        return {"temperature_c": temp, "sm_clock_mhz": clock, "power_w": power}
    except (OSError, subprocess.SubprocessError, ValueError):
        return {"temperature_c": 0.0, "sm_clock_mhz": 0.0, "power_w": 0.0}


def _load_items(cases_path: Path, model_dir: Path, max_items: int, max_len: int, head_max_len: int):
    import torch
    from laya.agent import _fix_tokenizer_config
    from transformers import AutoTokenizer
    from training.laya_finetune import build_training_items, load_cases

    cache = Path(os.environ.get("LAYA_BENCH_ITEMS", "/tmp/laya-optimization-items.pt"))
    if cache.is_file() and max_items >= 600 and max_len == 1024 and head_max_len == 256:
        return torch.load(cache, weights_only=False)[:max_items], AutoTokenizer.from_pretrained(model_dir / "tokenizer")
    _fix_tokenizer_config(str(model_dir))
    tokenizer = AutoTokenizer.from_pretrained(model_dir / "tokenizer")
    cases = load_cases(cases_path, split="train")[:max_items]
    items = build_training_items(cases, tokenizer, max_len=max_len, head_max_len=head_max_len)
    if max_items >= 600 and max_len == 1024 and head_max_len == 256:
        cache.parent.mkdir(parents=True, exist_ok=True)
        torch.save(items, cache)
    return items, tokenizer


def _build_model(model_dir: Path, profile: dict[str, Any], torch):
    from laya.common import build_model
    from peft import LoraConfig, TaskType, get_peft_model
    from safetensors.torch import load_file
    from training.laya_finetune import _lora_targets
    from training.laya_runtime import patch_peft_forward

    config = json.loads((model_dir / "rl_agent_config.json").read_text(encoding="utf-8"))
    config.update({"gradient_checkpointing": True, "max_len": profile["max_len"], "head_max_len": profile["head_max_len"]})
    model = build_model(config, encoder_dir=model_dir / "encoder")
    model.load_state_dict(load_file(str(model_dir / "model.safetensors")), strict=True)
    model.encoder.config._attn_implementation = profile["attention"]
    targets = _lora_targets(model)
    decision_modules = [
        name for name in ("head", "type_emb", "scorer", "act_head")
        if any(parameter_name == name or parameter_name.startswith(name + ".") for parameter_name, _ in model.named_parameters())
    ]
    patch_peft_forward(model)
    model = get_peft_model(
        model,
        LoraConfig(
            task_type=TaskType.FEATURE_EXTRACTION,
            r=8,
            lora_alpha=16,
            lora_dropout=0.05,
            target_modules=targets,
            modules_to_save=decision_modules or None,
        ),
    )
    if profile["checkpointing"]:
        model.encoder.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    else:
        model.encoder.gradient_checkpointing_disable()
    torch.set_float32_matmul_precision("high")
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    if profile["bf16_frozen"]:
        for parameter in model.parameters():
            if not parameter.requires_grad:
                parameter.data = parameter.data.to(torch.bfloat16)
    model.to("cuda")
    model.train()
    encoder_parameters = [parameter for name, parameter in model.named_parameters() if "lora_" in name]
    head_parameters = [parameter for name, parameter in model.named_parameters() if "lora_" not in name and parameter.requires_grad]
    optimizer_kwargs = {"fused": True} if profile["fused_adamw"] else {}
    optimizer = torch.optim.AdamW(
        [
            {"params": encoder_parameters, "lr": 2.5e-5},
            {"params": head_parameters, "lr": 1.0e-4},
        ],
        weight_decay=0.01,
        **optimizer_kwargs,
    )
    if profile["compile"]:
        model = torch.compile(model, mode="default", dynamic=False)
    return model, optimizer


def _run(profile: dict[str, Any], cases_path: Path, model_dir: Path, max_items: int, warmup: int, microsteps: int) -> dict[str, Any]:
    import torch
    from laya.common import proper_reward
    from training.laya_finetune import _collate

    if not torch.cuda.is_available():
        return {"status": "blocked", "reason": "cuda_unavailable", "profile": profile}
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    start_free, total_vram = torch.cuda.mem_get_info()
    items, tokenizer = _load_items(cases_path, model_dir, max_items, profile["max_len"], profile["head_max_len"])
    build_started = time.perf_counter()
    model, optimizer = _build_model(model_dir, profile, torch)
    build_seconds = time.perf_counter() - build_started
    if profile["compile"]:
        # Keep the compiled graph shape stable before timing the real loop.
        dummy = items[:profile["micro_batch"]]
        batch = _collate(dummy, tokenizer.pad_token_id, torch, fixed_option_width=profile["fixed_options"])
        with torch.autocast("cuda", dtype=torch.bfloat16):
            warm_logits, warm_act = model(
                input_ids=batch["input_ids"].cuda(),
                attention_mask=batch["attention_mask"].cuda(),
                marker_pos=batch["marker_pos"].cuda(),
                marker_mask=batch["marker_mask"].cuda(),
                qtype=batch["qtype"].cuda(),
            )
        (warm_logits.float().sum() + 0.0 * warm_act.sum()).backward()
        optimizer.zero_grad(set_to_none=True)
        torch.cuda.synchronize()
    optimizer.zero_grad(set_to_none=True)
    losses: list[float] = []
    entropies: list[float] = []
    loss_tensors: list[Any] = []
    entropy_tensors: list[Any] = []
    grad_norms: list[float] = []
    temperatures: list[float] = []
    powers: list[float] = []
    free_samples: list[int] = [int(torch.cuda.mem_get_info()[0])]
    finite = True
    sigma = 0.4
    torch.cuda.synchronize()
    started = time.perf_counter()
    total_steps = warmup + microsteps
    for step in range(total_steps):
        offset = (step * profile["micro_batch"]) % max(1, len(items) - profile["micro_batch"])
        batch = _collate(
            items[offset:offset + profile["micro_batch"]],
            tokenizer.pad_token_id,
            torch,
            fixed_option_width=profile["fixed_options"],
        )
        with torch.autocast("cuda", dtype=torch.bfloat16):
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
        probabilities = torch.softmax(logits.masked_fill(~mask, -1e4), -1)
        entropy = (-(probabilities * torch.log(probabilities.clamp_min(1e-9))).sum(-1) / torch.log(width.clamp_min(2.0))).mean()
        noise = torch.randn((4,) + logits.shape, device="cuda") * sigma * mask
        noise = (noise - noise.sum(-1, keepdim=True) / width) * mask
        sampled = logits.detach().unsqueeze(0) + noise
        with torch.no_grad():
            reward = proper_reward(
                torch.softmax(sampled.masked_fill(~mask, -1e4), -1),
                target.unsqueeze(0),
                batch["qtype"].cuda(),
                mask,
                w_sph=0.75,
                w_rps=1.0,
            )
            advantage = (reward - reward.mean(0, keepdim=True)) / (reward.std() + 1e-6)
        log_probability = -(((sampled - logits.unsqueeze(0)) ** 2) * mask).sum(-1) / (2 * sigma ** 2)
        objective = (-(advantage * log_probability).mean() - (target * torch.log_softmax(logits.masked_fill(~mask, -1e4), -1)).sum(-1).mean())
        objective = objective + 0.0 * act.sum()
        if profile["sync_metrics"]:
            entropy_value = float(entropy.detach().cpu())
            objective_value = float(objective.detach().cpu())
        else:
            entropy_value = None
            objective_value = None
        (objective / profile["accum"]).backward()
        if (step + 1) % profile["accum"] == 0 or step + 1 == total_steps:
            grad_norm = float(torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0).detach().cpu())
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
            if step >= warmup:
                grad_norms.append(grad_norm)
        if step >= warmup:
            if objective_value is not None:
                losses.append(objective_value)
                entropies.append(entropy_value)
            else:
                loss_tensors.append(objective.detach())
                entropy_tensors.append(entropy.detach())
        if step % max(1, total_steps // 8) == 0 or step + 1 == total_steps:
            free_samples.append(int(torch.cuda.mem_get_info()[0]))
            sample = _gpu_sample()
            temperatures.append(sample["temperature_c"])
            powers.append(sample["power_w"])
    torch.cuda.synchronize()
    if not profile["sync_metrics"] and loss_tensors:
        losses = [float(torch.stack(loss_tensors).mean().cpu())]
        entropies = [float(torch.stack(entropy_tensors).mean().cpu())]
    finite = bool(torch.isfinite(torch.stack(loss_tensors or [torch.tensor(losses)])).all().item())
    wall_seconds = max(1e-6, time.perf_counter() - started)
    peak_allocated = int(torch.cuda.max_memory_allocated())
    peak_reserved = int(torch.cuda.max_memory_reserved())
    return {
        "status": "ok" if finite else "nonfinite",
        "profile": profile,
        "build_seconds": round(build_seconds, 3),
        "warmup": warmup,
        "microsteps": microsteps,
        "wall_seconds": round(wall_seconds, 3),
        "examples_per_second": round(microsteps * profile["micro_batch"] / wall_seconds, 4),
        "tokens_per_second": round(microsteps * profile["micro_batch"] * profile["max_len"] / wall_seconds, 2),
        "mean_loss": round(statistics.fmean(losses), 6) if losses else None,
        "mean_entropy": round(statistics.fmean(entropies), 6) if entropies else None,
        "max_grad_norm_pre_clip": round(max(grad_norms), 6) if grad_norms else None,
        "peak_allocated_bytes": peak_allocated,
        "peak_reserved_bytes": peak_reserved,
        "peak_allocated_gib": round(peak_allocated / 2**30, 4),
        "peak_reserved_gib": round(peak_reserved / 2**30, 4),
        "start_free_vram_bytes": int(start_free),
        "min_free_vram_bytes": min(free_samples),
        "total_vram_bytes": int(total_vram),
        "max_temperature_c": max(temperatures) if temperatures else 0.0,
        "max_power_w": max(powers) if powers else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--cases", type=Path, default=Path("/tmp/factorio-laya-cases-all.jsonl"))
    parser.add_argument("--model-dir", type=Path, default=REPO_ROOT / "data/laya-base")
    parser.add_argument("--max-items", type=int, default=600)
    parser.add_argument("--warmup", type=int, default=4)
    parser.add_argument("--microsteps", type=int, default=96)
    parser.add_argument("--micro-batch", type=int, required=True)
    parser.add_argument("--accum", type=int, required=True)
    parser.add_argument("--checkpointing", action="store_true")
    parser.add_argument("--compile", action="store_true")
    parser.add_argument("--attention", choices=("sdpa", "flex_attention"), default="sdpa")
    parser.add_argument("--fixed-options", type=int, default=None)
    parser.add_argument("--bf16-frozen", action="store_true")
    parser.add_argument("--fused-adamw", action="store_true")
    parser.add_argument("--sync-metrics", action="store_true")
    args = parser.parse_args()
    profile = {
        "name": args.profile,
        "micro_batch": args.micro_batch,
        "accum": args.accum,
        "checkpointing": args.checkpointing,
        "compile": args.compile,
        "attention": args.attention,
        "fixed_options": args.fixed_options,
        "bf16_frozen": args.bf16_frozen,
        "fused_adamw": args.fused_adamw,
        "sync_metrics": args.sync_metrics,
        "max_len": 1024,
        "head_max_len": 256,
    }
    import torch
    try:
        report = _run(profile, args.cases, args.model_dir, args.max_items, args.warmup, args.microsteps)
    except torch.cuda.OutOfMemoryError as error:  # type: ignore[name-defined]
        report = {"status": "oom", "profile": profile, "error": str(error)[:500]}
    except Exception as error:
        report = {"status": "failed", "profile": profile, "error": f"{type(error).__name__}: {error}"[:500]}
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if report["status"] in {"ok", "blocked", "oom"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
