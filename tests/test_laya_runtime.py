# Path: tests/test_laya_runtime.py
# Purpose: Verify the local Laya runtime profile without requiring a GPU in CI.

from __future__ import annotations

import json
from pathlib import Path

import pytest

from training.laya_runtime import (
    Laya4060Config,
    LayaRuntimeError,
    autocast_dtype,
    preflight,
    resolve_sequence_version,
)
from training.laya_sequence import LEGACY_SEQUENCE_VERSION, SEQUENCE_VERSION


def test_default_profile_matches_local_4060_plan() -> None:
    config = Laya4060Config()
    assert config.profile_id == "laya-4060-lora"
    assert config.max_len == 1024
    assert config.micro_batch == 1
    assert config.gradient_accumulation == 32
    assert config.group_size == 4


def test_invalid_profile_limits_fail() -> None:
    with pytest.raises(ValueError):
        Laya4060Config(max_len=256)
    with pytest.raises(ValueError):
        Laya4060Config(min_free_ratio=1.0)
    with pytest.raises(ValueError):
        Laya4060Config(attention_implementation="eager")


def test_measured_fast_profile_is_explicit() -> None:
    config = Laya4060Config(
        micro_batch=2,
        gradient_accumulation=16,
        gradient_checkpointing=False,
        torch_compile=True,
        attention_implementation="flex_attention",
        fixed_option_width=6,
    )
    assert config.micro_batch == 2
    assert config.gradient_accumulation == 16
    assert config.gradient_checkpointing is False
    assert config.torch_compile is True
    assert config.attention_implementation == "flex_attention"
    assert config.fixed_option_width == 6


class _CudaUnavailable:
    @staticmethod
    def is_available():
        return False


class _TorchUnavailable:
    cuda = _CudaUnavailable()


def test_preflight_fails_closed_without_cuda() -> None:
    report = preflight(
        Laya4060Config(
            micro_batch=2,
            gradient_accumulation=16,
            gradient_checkpointing=False,
            torch_compile=True,
            attention_implementation="flex_attention",
            fixed_option_width=6,
        ),
        torch_module=_TorchUnavailable(),
    )
    assert report["status"] == "blocked"
    assert report["reason"] == "cuda_unavailable"
    assert report["micro_batch"] == 2
    assert report["gradient_checkpointing"] is False
    assert report["torch_compile"] is True
    assert report["attention_implementation"] == "flex_attention"
    assert report["fixed_option_width"] == 6
    assert autocast_dtype(_TorchUnavailable()) == "float16"


def test_checkpoint_sequence_format_is_explicit_and_legacy_requires_opt_in() -> None:
    assert resolve_sequence_version({"sequence_version": SEQUENCE_VERSION}) == SEQUENCE_VERSION
    assert resolve_sequence_version({}) == LEGACY_SEQUENCE_VERSION
    with pytest.raises(LayaRuntimeError, match="legacy truncating"):
        resolve_sequence_version({"fine_tuned": True, "checkpoint_format": "peft_lora"})
    assert resolve_sequence_version(
        {"fine_tuned": True}, allow_legacy_sequence=True,
    ) == LEGACY_SEQUENCE_VERSION
    with pytest.raises(LayaRuntimeError, match="unsupported"):
        resolve_sequence_version({"sequence_version": "future-v99"})


def test_existing_fine_tuned_adapter_is_identified_as_legacy() -> None:
    config_path = (
        Path(__file__).resolve().parents[1]
        / "data" / "laya-checkpoints" / "laya-flex-v1" / "rl_agent_config.json"
    )
    if not config_path.is_file():
        pytest.skip("local audited adapter metadata is unavailable")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    with pytest.raises(LayaRuntimeError, match="legacy truncating"):
        resolve_sequence_version(config)


def test_explicit_sequence_version_accepts_only_the_current_representation(tmp_path) -> None:
    from training.laya_runtime import LayaModelPredictor, LayaRuntimeError

    with pytest.raises(LayaRuntimeError, match="explicit sequence version"):
        LayaModelPredictor(tmp_path, explicit_sequence_version="factorio-decision-v1")
