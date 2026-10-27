# Path: tests/test_serve_laya_model.py | Purpose: Verify opt-in Kev reconstruction and single external calibration without model assets or serving.
from dataclasses import dataclass, field
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from tools import serve_laya_model as serving
from training.laya_model_policy import QUESTION, RENDERER, load_policy_bundle, numerical_contract

torch = pytest.importorskip("torch")
pytestmark = pytest.mark.training
TEMPERATURE = 2.3510958125672174


def contract(dtype="bf16"):
    return {"version": "kev-cpu-fp32-v1", "checkpoint_weights_dtype": dtype,
            "external_temperature": TEMPERATURE}


@dataclass
class Meta:
    weights_dtype: str = "bf16"
    temperature: float = 9.0
    head: dict = field(default_factory=dict)
    base: str = "frozen-base"
    base_revision: str = "frozen-revision"


@dataclass
class LoadOptions:
    dtype: object = None
    backend: str = "torch"
    attn: str = "eager"
    merge: bool = True
    cuda_graphs: bool = False
    fused: bool = False
    temperature: float | None = None


class TinyModel(torch.nn.Module):
    backend = "torch"
    hybrid = False

    def __init__(self, dtype=torch.float32):
        super().__init__()
        self.lm = torch.nn.Linear(2, 2, bias=False, dtype=dtype)
        self.head = torch.nn.Linear(2, 2, bias=False)
        self.head.temperature = 1.0
        self.register_buffer("floating", torch.ones(1, dtype=dtype))
        self.register_buffer("indices", torch.ones(1, dtype=torch.int64))

    def forward(self, enc):
        # Fixture logits follow Kev PointerHead's eval-mode temperature rule.
        return [torch.tensor([0.25, 1.75]) / self.head.temperature]


class Adapter(torch.nn.Module):
    def __init__(self, base):
        super().__init__()
        self.base = base

    @classmethod
    def from_pretrained(cls, base, path, **kwargs):
        return cls(base)

    def merge_and_unload(self):
        return self.base


class Tuner(torch.nn.Module):
    pass


@pytest.fixture
def fake_peft(monkeypatch):
    peft = ModuleType("peft")
    peft.PeftModel = Adapter
    tuners = ModuleType("peft.tuners.tuners_utils")
    tuners.BaseTunerLayer = Tuner
    monkeypatch.setitem(sys.modules, "peft", peft)
    monkeypatch.setitem(sys.modules, "peft.tuners.tuners_utils", tuners)


class Checkpoint:
    full = False

    def __init__(self, path=None):
        self.meta = Meta()
        self.loads = []

    def load(self, device, opts):
        self.loads.append((self.meta, opts))
        # The native loader's BF16 provenance override, including merge=False.
        bf16 = self.meta.weights_dtype == "bf16"
        model = TinyModel(torch.bfloat16 if bf16 else opts.dtype)
        if bf16 or not opts.merge:
            model.lm = Adapter(model.lm)
        model.head.temperature = self.meta.temperature if opts.temperature is None else opts.temperature
        return "tokenizer", model.eval()


def test_bf16_reconstruction_preserves_original_metadata_and_saved_head(fake_peft):
    ck = Checkpoint()
    ck.meta.head["saved"] = torch.tensor([3.0])
    original = ck.meta
    tok, model, runtime = serving.load_kev_checkpoint(ck, {"model": "kev", "numerical_contract": contract()}, LoadOptions)
    loading_meta, opts = ck.loads[0]
    assert ck.meta is original and ck.meta.weights_dtype == "bf16" and ck.meta.temperature == 9.0
    assert loading_meta is not original and loading_meta.head is original.head
    assert torch.equal(original.head["saved"], torch.tensor([3.0]))
    assert loading_meta.weights_dtype == "fp32" and opts.temperature == 1.0
    assert opts.merge and opts.dtype == torch.float32 and opts.backend == "torch"
    assert opts.attn == "eager" and opts.fused is False and opts.cuda_graphs is False
    assert tok == "tokenizer" and next(model.parameters()).dtype == torch.float32
    assert runtime["checkpoint_weights_dtype"] == "bf16" and runtime["load_weights_dtype"] == "fp32"
    assert runtime["adapter_merged"] is True and runtime["parameter_dtypes"] == ["float32"]
    assert runtime["buffer_dtypes"] == ["float32", "int64"]


def test_contract_precision_pin_rejects_before_loading(fake_peft):
    ck = Checkpoint()
    with pytest.raises(ValueError, match="training precision"):
        serving.load_kev_checkpoint(ck, {"model": "kev", "numerical_contract": contract("fp32")}, LoadOptions)
    assert ck.loads == []


@pytest.mark.parametrize("fault", ["parameter", "buffer", "adapter", "tuner", "temperature", "training"])
def test_effective_numerics_fail_closed(fake_peft, fault):
    class BadCheckpoint(Checkpoint):
        def load(self, device, opts):
            tok, model = super().load(device, opts)
            if fault == "parameter":
                model.lm.to(torch.bfloat16)
            elif fault == "buffer":
                model.floating = model.floating.to(torch.bfloat16)
            elif fault == "adapter":
                model.lm = Adapter(model.lm)
            elif fault == "tuner":
                model.lm.add_module("remaining_adapter", Tuner())
            elif fault == "temperature":
                model.head.temperature = 3.0
            else:
                model.train()
            return tok, model
    with pytest.raises(ValueError, match="CPU FP32|merged adapter|raw head logits"):
        serving.load_kev_checkpoint(BadCheckpoint(), {"model": "kev", "numerical_contract": contract()}, LoadOptions)


@pytest.mark.parametrize("checkpoint_temperature", [1.0, 9.0, TEMPERATURE])
@pytest.mark.parametrize("strict", [False, True])
def test_scorer_temperature_precedence_and_no_double_calibration(monkeypatch, fake_peft, checkpoint_temperature, strict):
    ck = Checkpoint()
    ck.meta.temperature = checkpoint_temperature
    bundle = {"model": "kev", "weights": "unused", "base_repo": ck.meta.base, "base_revision": ck.meta.base_revision}
    if strict:
        bundle["numerical_contract"] = contract()
    api = ModuleType("kev.api")
    api.SystemOneRequest = SimpleNamespace(model_validate=lambda body: body)
    api.to_record = lambda body: (body, [{"keys": ["a", "b"]}])
    checkpoint = ModuleType("kev.checkpoint")
    checkpoint.Checkpoint = lambda path: ck
    checkpoint.LoadOptions = LoadOptions
    model_module = ModuleType("kev.model")
    model_module.admit = lambda *args, **kwargs: {"ids": [1, 2], "state_tokens": 1}
    for name, module in (("kev", ModuleType("kev")), ("kev.api", api), ("kev.checkpoint", checkpoint), ("kev.model", model_module)):
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(torch, "set_num_interop_threads", lambda n: None)
    monkeypatch.setattr(torch, "set_num_threads", lambda n: None)
    monkeypatch.setattr(serving, "version", lambda name: "fixture")
    predict, runtime = serving.scorer(bundle)
    raw, calibrated, _ = predict({"questions": {QUESTION: {}}})
    z = torch.tensor([0.25, 1.75], dtype=torch.float64)
    temperature = TEMPERATURE if strict else checkpoint_temperature
    assert list(raw.values()) == pytest.approx(torch.softmax(z, -1).tolist(), abs=1e-7)
    assert list(calibrated.values()) == pytest.approx(torch.softmax(z / temperature, -1).tolist(), abs=1e-7)
    assert runtime["temperature"] == temperature
    assert ck.meta.temperature == checkpoint_temperature and ck.meta.weights_dtype == "bf16"
    assert ck.loads[0][1].temperature == (1.0 if strict else None)
    if not strict:
        assert isinstance(ck.loads and ck.loads[0][0], Meta)
        assert "numerical_contract" not in runtime


@pytest.mark.parametrize("bad", [None, {}, {**contract(), "version": "unknown"},
    {**contract(), "checkpoint_weights_dtype": "fp16"}, {**contract(), "extra": True},
    *[{**contract(), "external_temperature": t} for t in (0, -1, True, "2", float("nan"), float("inf"))]])
def test_bundle_rejects_invalid_contract_before_artifact_io(tmp_path, bad):
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps({"version": "laya-model-policy-v1", "renderer": RENDERER,
                               "model": "kev", "numerical_contract": bad}))
    with pytest.raises(ValueError, match="contract|temperature"):
        load_policy_bundle(path)


def test_contract_is_explicit_and_kev_only():
    assert numerical_contract({"model": "kev"}) is None
    with pytest.raises(ValueError, match="contract"):
        numerical_contract({"model": "jiwo", "numerical_contract": contract()})


def test_pinned_native_loader_bf16_override_and_contract(tmp_path, monkeypatch, fake_peft):
    """Optional source integration: execute the pinned native loader with a tiny backbone."""
    source = os.environ.get("LAYA_KEV_CHECKPOINT_SOURCE")
    if not source:
        pytest.skip("set LAYA_KEV_CHECKPOINT_SOURCE to qualify the pinned native loader")
    package = "_laya_kev_contract_test"
    model_module = ModuleType(f"{package}.model")
    model_module.DecisionModel = lambda *args, **kwargs: TinyModel(kwargs["dtype"])
    model_module.is_hybrid = lambda config: False
    model_module.load_tokenizer = lambda *args, **kwargs: "tokenizer"
    model_module.pad_id = lambda tok: 0
    monkeypatch.setitem(sys.modules, package, ModuleType(package))
    monkeypatch.setitem(sys.modules, f"{package}.model", model_module)
    spec = importlib.util.spec_from_file_location(f"{package}.checkpoint", Path(source))
    native = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, native)
    spec.loader.exec_module(native)
    (tmp_path / "adapter_config.json").write_text("{}")
    ck = native.Checkpoint.__new__(native.Checkpoint)
    ck.path = ck.requested = str(tmp_path)
    ck.meta = native.Meta(base="fixture", weights_dtype="bf16", temperature=9.0,
                          head=TinyModel().head.state_dict())
    # The actual native implementation ignores the requested FP32 and merge.
    _, legacy = ck.load("cpu", native.LoadOptions(dtype=torch.float32, merge=True, fused=False))
    assert isinstance(legacy.lm, Adapter) and next(legacy.lm.parameters()).dtype == torch.bfloat16
    assert legacy.head.temperature == 9.0
    _, exact, runtime = serving.load_kev_checkpoint(ck, {"model": "kev", "numerical_contract": contract()}, native.LoadOptions)
    assert not isinstance(exact.lm, Adapter) and next(exact.lm.parameters()).dtype == torch.float32
    assert exact.head.temperature == 1.0 and runtime["temperature"] == TEMPERATURE
    assert ck.meta.weights_dtype == "bf16" and ck.meta.temperature == 9.0
