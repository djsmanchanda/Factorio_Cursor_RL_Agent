# Path: tests/test_training_compute.py
# Purpose: Verify optional CUDA discovery cannot disrupt CPU-only training.

from __future__ import annotations

from types import ModuleType

import pytest

from training import compute


class _CudaAvailable:
    @staticmethod
    def is_available() -> bool:
        return True


class _CudaUnavailable:
    @staticmethod
    def is_available() -> bool:
        return False


def _fake_torch(cuda: object) -> ModuleType:
    module = ModuleType("torch")
    module.cuda = cuda
    return module


def test_cpu_preference_never_imports_optional_torch(monkeypatch) -> None:
    def forbidden_import() -> ModuleType:
        raise AssertionError("CPU selection must not import PyTorch")

    monkeypatch.setattr(compute, "_import_torch", forbidden_import)

    runtime = compute.resolve_training_compute("cpu")

    assert runtime.metadata() == {
        "requested": "cpu", "device": "cpu", "backend": None,
        "uses_cuda": False, "reason": "CPU requested",
    }
    assert runtime.torch is None


def test_auto_falls_back_when_pytorch_is_missing(monkeypatch) -> None:
    def missing_torch() -> ModuleType:
        raise ImportError("no module named torch")

    monkeypatch.setattr(compute, "_import_torch", missing_torch)

    runtime = compute.resolve_training_compute()

    assert runtime.device == "cpu"
    assert runtime.backend is None
    assert not runtime.uses_cuda
    assert "not installed" in runtime.reason


def test_cuda_request_falls_back_when_driver_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(compute, "_import_torch", lambda: _fake_torch(_CudaUnavailable()))

    runtime = compute.resolve_training_compute("cuda")

    assert runtime.requested == "cuda"
    assert runtime.device == "cpu"
    assert runtime.backend == "torch"
    assert runtime.torch is not None
    assert "unavailable" in runtime.reason


def test_auto_selects_cuda_and_exposes_torch_only_after_opt_in(monkeypatch) -> None:
    torch = _fake_torch(_CudaAvailable())
    monkeypatch.setattr(compute, "_import_torch", lambda: torch)

    runtime = compute.resolve_training_compute()

    assert runtime.uses_cuda
    assert runtime.device == "cuda"
    assert runtime.backend == "torch"
    assert runtime.torch is torch
    assert runtime.metadata()["reason"] == "CUDA available"


def test_invalid_compute_preference_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown training compute preference"):
        compute.resolve_training_compute("neural-engine")
