# Path: tests/test_laya_finetune.py
# Purpose: Verify local fine-tuning configuration and dataset split handling without ML dependencies.

from __future__ import annotations

import json

import pytest

from training.laya_dataset import seed_split
from training.laya_finetune import (
    LayaFineTuneConfig,
    LayaFineTuneError,
    _checkpoint_interval,
    _collate,
    _resolve_resume_checkpoint,
    _weighted_mean,
    load_cases,
    train_local,
)


def test_finetune_profile_is_single_gpu_lora_ready() -> None:
    config = LayaFineTuneConfig()
    assert config.runtime.profile_id == "laya-4060-lora"
    assert config.lora_rank == 8
    assert config.runtime.group_size == 4


def test_load_cases_respects_split(tmp_path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        json.dumps({"case_id": "train", "split": "train"}) + "\n"
        + json.dumps({"case_id": "held", "split": "held_out"}) + "\n",
        encoding="utf-8",
    )
    assert [case["case_id"] for case in load_cases(path)] == ["train"]
    assert [case["case_id"] for case in load_cases(path, split="held_out")] == ["held"]


def test_load_cases_rejects_empty_split(tmp_path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps({"split": "train"}) + "\n", encoding="utf-8")
    with pytest.raises(LayaFineTuneError, match="no 'calibration' cases"):
        load_cases(path, split="calibration")


def test_seed_split_is_stable_and_known() -> None:
    assert seed_split(12345) == seed_split(12345)
    assert seed_split(12345) in {"train", "calibration", "held_out", "test"}


def test_train_local_reports_missing_paths_before_gpu_work(tmp_path) -> None:
    with pytest.raises(LayaFineTuneError, match="dataset file does not exist"):
        train_local(tmp_path / "missing.jsonl", tmp_path / "model", tmp_path / "out")


def test_compile_profile_requires_static_option_width(tmp_path) -> None:
    config = LayaFineTuneConfig(torch_compile=True)
    with pytest.raises(LayaFineTuneError, match="static option shape"):
        train_local(tmp_path / "missing.jsonl", tmp_path / "model", tmp_path / "out", config=config)


def test_collate_can_hold_one_static_option_width() -> None:
    torch = pytest.importorskip("torch")
    item = {"ids": [1, 2], "markers": [0, 1], "qtype": 0, "target": [0.5, 0.5]}
    batch = _collate([item], 0, torch, fixed_option_width=6)
    assert tuple(batch["marker_pos"].shape) == (1, 6)
    assert tuple(batch["target"].shape) == (1, 6)
    assert batch["marker_mask"].tolist() == [[True, True, False, False, False, False]]
    assert batch["weight"].tolist() == [1.0]


def test_weighted_mean_uses_question_weights_on_cpu() -> None:
    torch = pytest.importorskip("torch")
    values = torch.tensor([1.0, 5.0])
    weights = torch.tensor([3.0, 1.0])
    assert float(_weighted_mean(values, weights, torch)) == pytest.approx(4.0)
    assert float(_weighted_mean(torch.tensor([4.0]), torch.tensor([0.25]), torch)) == pytest.approx(1.0)


def test_checkpoint_interval_and_latest_resolution(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LAYA_CHECKPOINT_INTERVAL", "100")
    assert _checkpoint_interval() == 100
    checkpoints = tmp_path / "checkpoints"
    for step in (250, 500):
        directory = checkpoints / f"step-{step:08d}"
        directory.mkdir(parents=True)
        (directory / "trainer_state.pt").write_bytes(b"state")
    monkeypatch.setenv("LAYA_RESUME_CHECKPOINT", "latest")
    assert _resolve_resume_checkpoint(tmp_path) == checkpoints / "step-00000500"


def test_explicit_adapter_checkpoint_can_be_warm_started(tmp_path, monkeypatch) -> None:
    checkpoint = tmp_path / "completed-adapter"
    checkpoint.mkdir()
    (checkpoint / "adapter_model.safetensors").write_bytes(b"adapter")
    monkeypatch.setenv("LAYA_RESUME_CHECKPOINT", str(checkpoint))
    assert _resolve_resume_checkpoint(tmp_path) == checkpoint


def test_checkpoint_interval_rejects_negative_values(monkeypatch) -> None:
    monkeypatch.setenv("LAYA_CHECKPOINT_INTERVAL", "-1")
    with pytest.raises(LayaFineTuneError, match="non-negative"):
        _checkpoint_interval()
