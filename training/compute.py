# Path: training/compute.py
# Purpose: Lazily discover an optional accelerator for future training-only models.

"""Optional compute runtime selection without making PyTorch a runtime dependency.

The current episode controller and diagonal LinUCB policy intentionally stay on the
standard library.  Larger, batched learner components can opt into this module to
obtain a CUDA-backed PyTorch module when it is genuinely available, while every
other environment receives an explicit CPU runtime and a diagnostic reason.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from types import ModuleType


_PREFERENCES = frozenset({"auto", "cpu", "cuda"})


@dataclass(frozen=True)
class TrainingComputeRuntime:
    """The resolved device and optional backend for a training-only component."""

    requested: str
    device: str
    backend: str | None
    reason: str
    _torch: ModuleType | None = field(default=None, repr=False, compare=False)

    @property
    def uses_cuda(self) -> bool:
        """Whether a caller may create tensors on the NVIDIA CUDA device."""
        return self.device == "cuda" and self.backend == "torch" and self._torch is not None

    @property
    def torch(self) -> ModuleType | None:
        """The lazily imported PyTorch module, if this runtime selected it."""
        return self._torch

    def metadata(self) -> dict[str, str | bool | None]:
        """Return JSON-safe observability data without exposing a module object."""
        return {
            "requested": self.requested,
            "device": self.device,
            "backend": self.backend,
            "uses_cuda": self.uses_cuda,
            "reason": self.reason,
        }


def _import_torch() -> ModuleType:
    """Import PyTorch only when a component explicitly probes acceleration."""
    return importlib.import_module("torch")


def resolve_training_compute(preference: str = "auto") -> TrainingComputeRuntime:
    """Choose a safe optional CUDA runtime for an opt-in training component.

    ``cpu`` never attempts to import PyTorch.  ``auto`` and ``cuda`` both fall
    back to CPU when PyTorch, CUDA, or the CUDA driver is unavailable; callers can
    surface the returned reason in observability instead of failing a batch.
    """
    normalized = str(preference).strip().lower()
    if normalized not in _PREFERENCES:
        raise ValueError(f"unknown training compute preference: {preference!r}")
    if normalized == "cpu":
        return TrainingComputeRuntime("cpu", "cpu", None, "CPU requested")

    try:
        torch = _import_torch()
    except ImportError:
        return TrainingComputeRuntime(
            normalized, "cpu", None, "PyTorch is not installed; using CPU",
        )
    except Exception as exc:  # Third-party import failures must not stop Factorio training.
        return TrainingComputeRuntime(
            normalized, "cpu", None, f"PyTorch could not load: {type(exc).__name__}; using CPU",
        )

    try:
        cuda = getattr(torch, "cuda")
        cuda_available = bool(cuda.is_available())
    except Exception as exc:  # A broken driver behaves like an unavailable optional backend.
        return TrainingComputeRuntime(
            normalized, "cpu", None, f"CUDA probe failed: {type(exc).__name__}; using CPU",
        )
    if not cuda_available:
        return TrainingComputeRuntime(
            normalized, "cpu", "torch", "CUDA is unavailable; using CPU", torch,
        )
    return TrainingComputeRuntime(normalized, "cuda", "torch", "CUDA available", torch)
