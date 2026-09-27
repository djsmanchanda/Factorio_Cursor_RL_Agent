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


# --- Resume order, optimizer-step accounting, signals, integrity, smoothing ---

import math
import os
import signal
from types import SimpleNamespace

from training import laya_finetune as finetune


def test_epoch_order_is_pure_so_resume_rebuilds_the_same_batches() -> None:
    lengths = [5, 90, 12, 40, 7, 64, 33, 2, 18, 71]
    first = [finetune._epoch_batches(lengths, seed=42, epoch=epoch, micro_batch=1) for epoch in range(3)]
    # A resumed run computes epoch 2 directly; it must not depend on epochs 0-1.
    assert finetune._epoch_batches(lengths, seed=42, epoch=2, micro_batch=1) == first[2]
    assert first[0] != first[1]
    assert sorted(index for batch in first[1] for index in batch) == list(range(len(lengths)))


def test_length_bucketing_keeps_batch_count_and_reduces_padding() -> None:
    lengths = [(index * 37) % 101 + 1 for index in range(64)]

    def padding(batches):
        return sum(max(lengths[i] for i in batch) * len(batch) - sum(lengths[i] for i in batch) for batch in batches)

    plain = finetune._epoch_batches(lengths, seed=1, epoch=0, micro_batch=4, bucket_batches=0)
    bucketed = finetune._epoch_batches(lengths, seed=1, epoch=0, micro_batch=4, bucket_batches=4)
    assert len(plain) == len(bucketed) == math.ceil(len(lengths) / 4)
    assert sorted(i for batch in bucketed for i in batch) == list(range(len(lengths)))
    assert padding(bucketed) < padding(plain)


def test_optimizer_steps_are_unique_and_match_scheduler_total() -> None:
    items, micro, accumulation, epochs = 100, 1, 32, 3  # 100 = 3 full windows + a 4-batch tail
    per_epoch = finetune._updates_per_epoch(items, micro, accumulation)
    step = 0
    seen = []
    for _epoch in range(epochs):
        batch_count = math.ceil(items / micro)
        window_total = 0
        for index in range(batch_count):
            if finetune._is_window_end(index, batch_count, accumulation):
                window_total += finetune._window_size(index, batch_count, accumulation)
                step += 1
                seen.append(step)
        assert window_total == batch_count  # each micro-batch belongs to exactly one window
    assert per_epoch == 4
    assert step == per_epoch * epochs  # equals CosineAnnealingLR T_max
    assert len(seen) == len(set(seen))
    assert finetune._window_size(99, 100, 32) == 4


def test_default_checkpoint_interval_is_short(monkeypatch) -> None:
    monkeypatch.delenv("LAYA_CHECKPOINT_INTERVAL", raising=False)
    assert _checkpoint_interval() == 25


def test_sigterm_requests_checkpoint_then_restores_handlers() -> None:
    previous = signal.getsignal(signal.SIGTERM)
    stop = finetune._StopRequest().install()
    try:
        os.kill(os.getpid(), signal.SIGTERM)
        assert stop.requested and stop.exit_code == 143
        assert signal.getsignal(signal.SIGTERM) == previous
    finally:
        stop.restore()


def test_resume_refuses_changed_dataset_and_old_format() -> None:
    expected = {"items_count": 3, "dataset_sha256": "a" * 64}
    state = {"format": finetune.CHECKPOINT_FORMAT, "items_count": 3, "dataset_sha256": "b" * 64}
    assert set(finetune._resume_mismatches(state, expected)) == {"dataset_sha256"}
    assert set(finetune._resume_mismatches({**state, "format": "laya_training_checkpoint_v1"}, expected)) == {"format"}


def test_label_smoothing_only_touches_one_hot_rows() -> None:
    torch = pytest.importorskip("torch")
    target = torch.tensor([[1.0, 0.0, 0.0, 0.0], [0.7, 0.3, 0.0, 0.0]])
    mask = torch.tensor([[True, True, False, False], [True, True, False, False]])
    smoothable = torch.tensor([True, False])
    smoothed = finetune._smooth_targets(target, mask, smoothable, 0.1, torch)
    assert smoothed[0].tolist() == pytest.approx([0.95, 0.05, 0.0, 0.0])
    assert smoothed[1].tolist() == pytest.approx([0.7, 0.3, 0.0, 0.0])
    assert finetune._smooth_targets(target, mask, smoothable, 0.0, torch) is target


def test_early_stopper_patience_and_state_round_trip() -> None:
    stopper = finetune._EarlyStopper(patience=1)
    assert stopper.update(1.0, 0) is True
    assert stopper.update(0.9, 1) is True
    assert stopper.update(0.95, 2) is False and stopper.stopped
    restored = finetune._EarlyStopper(patience=1)
    restored.restore(stopper.state())
    assert (restored.best, restored.best_epoch, restored.stopped) == (0.9, 1, True)
    assert finetune._EarlyStopper(patience=0).update(1.0, 0) and not finetune._EarlyStopper(patience=0).stopped


def test_training_items_keep_soft_targets_and_count_context_overflow(monkeypatch) -> None:
    from training.laya_sequence import LayaSequenceError

    def build_sequence(tokenizer, state, question, max_len, head_max_len):
        if state.get("too_long"):
            raise LayaSequenceError("overflow")
        return [1, 2, 3], list(range(len(question["crit"])))

    monkeypatch.setattr(finetune, "_require_training_dependencies", lambda: (
        None, {"choice": 0, "noul": 1, "score": 2}, build_sequence, None,
        lambda question: list(question["crit"]), None, "factorio-decision-v2",
    ))
    criteria = {"goal.iron": "Iron", "goal.copper": "Copper"}

    def case(probabilities, **state):
        return {"state": state, "questions": {"serve_goal": {"type": "choice", "instructions": "x", "criteria": criteria}},
                "gold": {"serve_goal": {"label": "goal.iron", "probabilities": probabilities}}}

    stats: dict = {}
    items = finetune.build_training_items(
        [case({"goal.iron": 0.7, "goal.copper": 0.3}), case({"goal.iron": 1.0}), case({"goal.iron": 1.0}, too_long=True)],
        None, max_len=1024, head_max_len=256, stats=stats,
    )
    assert [item["target"] for item in items] == [[0.7, 0.3], [1.0, 0.0]]
    assert [item["smoothable"] for item in items] == [False, True]
    assert stats["context_overflow"] == 1


def test_collate_rounds_length_only_when_asked() -> None:
    torch = pytest.importorskip("torch")
    item = {"ids": [1, 2, 3], "markers": [0, 1], "qtype": 0, "target": [1.0, 0.0], "smoothable": True}
    assert tuple(_collate([item], 0, torch)["input_ids"].shape) == (1, 3)
    batch = _collate([item], 0, torch, fixed_option_width=4, pad_to_multiple=64)
    assert tuple(batch["input_ids"].shape) == (1, 64)
    assert batch["attention_mask"].sum().item() == 3
    assert batch["smoothable"].tolist() == [True]


def test_periodic_checkpoint_carries_inference_context(tmp_path) -> None:
    class Model:
        def save_pretrained(self, path, safe_serialization=True):
            (path / "adapter_model.safetensors").write_bytes(b"adapter")

    stateful = SimpleNamespace(state_dict=lambda: {})
    fake_torch = SimpleNamespace(
        save=lambda obj, path: path.write_text(json.dumps({"format": obj["format"], "next_batch": obj["next_batch"]})),
        get_rng_state=lambda: None,
        cuda=SimpleNamespace(get_rng_state_all=lambda: []),
    )
    root = tmp_path / "checkpoints"
    final = finetune._save_training_checkpoint(
        model=Model(), optimizer=stateful, scheduler=stateful, scaler=stateful, torch=fake_torch,
        checkpoint_root=root, optimizer_steps=25, keep_count=2,
        trainer_state={"next_epoch": 0, "next_batch": 800, "dataset_sha256": "a" * 64},
        inference_config={"max_len": 2048, "head_max_len": 256, "sequence_version": "factorio-decision-v2"},
    )
    assert final.name == "step-00000025"
    assert json.loads((final / "rl_agent_config.json").read_text())["max_len"] == 2048
    assert json.loads((final / "checkpoint.json").read_text())["dataset_sha256"] == "a" * 64
    assert (root / "latest").read_text().strip() == "step-00000025"


def test_long_context_env_profile_forces_checkpointing(monkeypatch) -> None:
    monkeypatch.setenv("LAYA_MAX_LEN", "2048")
    monkeypatch.delenv("LAYA_GRADIENT_CHECKPOINTING", raising=False)
    monkeypatch.delenv("LAYA_MICRO_BATCH", raising=False)
    config = finetune.config_from_env()
    assert (config.runtime.max_len, config.runtime.micro_batch, config.gradient_checkpointing) == (2048, 1, True)
    monkeypatch.setenv("LAYA_MICRO_BATCH", "2")
    with pytest.raises(LayaFineTuneError, match="micro_batch=1"):
        finetune.config_from_env()


def test_training_scripts_share_durable_dirs_and_guard_before_status() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "scripts"
    single = (root / "run_laya_training.sh").read_text()
    resumable = (root / "run_laya_training_resumable.sh").read_text()
    assert single.index("pgrep -f") < single.index('"status":"starting"')
    for text in (single, resumable):
        assert "data/laya-checkpoints/laya-4060" in text and "/tmp/" not in text
    assert "--step-level" in resumable and "find \"$REPO_ROOT/data\"" not in resumable


def test_resumable_autobuild_requires_explicit_inputs(tmp_path) -> None:
    import subprocess
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "run_laya_training_resumable.sh"
    env = {**os.environ, "LAYA_CASES_PATH": str(tmp_path / "missing.jsonl"), "LAYA_CHECKPOINT_DIR": str(tmp_path / "ckpt")}
    env.pop("LAYA_DATASET_DATABASES", None)
    env.pop("LAYA_DATASET_CASES_JSONL", None)
    result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert "LAYA_DATASET_DATABASES" in result.stderr
