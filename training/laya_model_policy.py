# Path: training/laya_model_policy.py | Purpose: Complete-menu System One rendering and checkpoint-backed seeded operation selection.
"""The model chooses an offered operation; the deterministic library executes it.

HTTP is a process boundary for incompatible model environments. There is no
fallback, menu pruning, preference multiplication, or implicit truncation.
"""
from __future__ import annotations

import hashlib
from collections import Counter
import json
import math
import random
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

RENDERER = "factorio-system-one-v2"
QUESTION = "select_operation"
INSTRUCTIONS = "Choose the next useful offered Factorio operation for the current goal, using the observed facts, costs, pending work and past outcomes."
OPTION_FIELDS = ("family", "need_id", "product", "params", "features", "bill", "bill_owner", "wait_ticks", "base_wait_ticks")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def render(state, menu):
    """Use structured descriptions identically for old records and live menus.

    Historical journals lack free-text summaries. The declared input contract
    therefore describes each action using its complete structured option facts;
    summaries are retained in evidence, never synthesized from future outcomes.
    """
    options = menu["options"]
    ids = [o["option_id"] for o in options]
    if not 1 <= len(ids) <= 255 or len(set(ids)) != len(ids) or any(not isinstance(i, str) or not i for i in ids):
        raise ValueError("invalid or unsupported complete menu")
    obs = state.get("observation_digest", {})
    saved = obs.get("saved_world", {})
    # The runner's causal brief is its existing bounded observation contract.
    # Do not duplicate the much larger raw census into the model context.
    projected = {"brief": re.sub(r"\[mission\.[^\]]*\]", "", state.get("brief") or ""), "rules": [g["statement"] for g in state.get("guidelines", [])],
                 "phase": obs.get("mission_phase"),
                 "stock": saved.get("provider_stock", {}).get("network"),
                 "reserved": saved.get("reserved_stock", {}).get("network")}
    columns = sorted({k for o in options for k in (o.get("features") or {})})
    projected["feature_columns"] = columns
    projected["encoding"] = "Option features use numeric keys indexing feature_columns. $ref names a value in shared_facts. Omitted values are unknown. Construction geometry stays in the executor; gate previews show the current/target gate and active bill shortage."
    criteria = {}
    for o in options:
        fields = {k: o[k] for k in OPTION_FIELDS if k != "features" and o.get(k) is not None}
        fields["features"] = {str(columns.index(k)): v for k, v in (o.get("features") or {}).items()}
        params = fields.get("params")
        if isinstance(params, dict) and isinstance(params.get("preview"), dict):
            preview = params["preview"]
            bill = preview.get("bill") or {}
            fields["params"] = {**params, "preview": {
                **{k: preview[k] for k in ("bill_owner", "cost", "current_gate", "target_gate") if k in preview},
                "bill": {k: bill[k] for k in ("active", "state", "target_item", "missing") if k in bill}}}
        criteria[o["option_id"]] = fields
    counts = Counter()

    def scan(value):
        if isinstance(value, (dict, list)):
            key = canonical(value)
            if len(key) > 80:
                counts[key] += 1
            for child in value.values() if isinstance(value, dict) else value:
                scan(child)

    for value in criteria.values():
        scan(value)
    shared, names = {}, {}

    def intern(value, definition=False):
        if isinstance(value, (dict, list)):
            key = canonical(value)
            if counts[key] > 1 and not definition:
                if key not in names:
                    name = f"fact{len(names)}"
                    names[key] = name
                    shared[name] = intern(value, True)
                return {"$ref": names[key]}
            return ({k: intern(v) for k, v in value.items()} if isinstance(value, dict) else [intern(v) for v in value])
        return value

    descriptions = {key: canonical(intern(value)) for key, value in criteria.items()}
    projected["shared_facts"] = shared
    return {"state": canonical(projected), "questions": {QUESTION: {
        "type": "choice", "instructions": INSTRUCTIONS,
        "criteria": descriptions}}}


def probabilities(values, ids):
    if not isinstance(values, dict) or set(values) != set(ids):
        raise ValueError("model probability IDs differ from the complete offered menu")
    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in values.values()):
        raise ValueError("invalid model probabilities")
    total = sum(values.values())
    if abs(total - 1) > 1e-5:
        raise ValueError("model probabilities must sum to one")
    return {k: values[k] / total for k in ids}


# Pinned Kev numerical paths and the checkpoint training precisions each accepts.
# kev-cuda-bf16-v1 is the weak-v3 offline evaluation's GPU path.
NUMERICAL_CONTRACTS = {"kev-cpu-fp32-v1": ("fp32", "bf16"), "kev-cuda-bf16-v1": ("bf16",)}


def numerical_contract(bundle):
    """An absent contract preserves the released loader's numerical behavior."""
    if "numerical_contract" not in bundle:
        return None
    contract = bundle["numerical_contract"]
    if (bundle.get("model") != "kev" or not isinstance(contract, dict)
            or set(contract) != {"version", "checkpoint_weights_dtype", "external_temperature"}
            or contract.get("checkpoint_weights_dtype") not in NUMERICAL_CONTRACTS.get(contract.get("version"), ())):
        raise ValueError("unsupported numerical contract")
    temperature = contract["external_temperature"]
    if type(temperature) not in (int, float) or not math.isfinite(temperature) or temperature <= 0:
        raise ValueError("external temperature must be finite and positive")
    return contract


def local_path(name, roots=None):
    """Where a serving host keeps an artifact; ``roots`` maps path prefixes to the same bytes elsewhere."""
    for source, target in (roots or {}).items():
        if name == source or name.startswith(source.rstrip("/") + "/"):
            return Path(target) / Path(name).relative_to(source)
    return Path(name)


def load_policy_bundle(path, roots=None):
    """Verify every artifact hash; the identity is the bundle's own digest, independent of ``roots``."""
    path = Path(path).resolve()
    bundle = json.loads(path.read_text())
    if bundle["version"] != "laya-model-policy-v1" or bundle["renderer"] != RENDERER:
        raise ValueError("unsupported model bundle or renderer")
    numerical_contract(bundle)
    if not bundle.get("files"):
        raise ValueError("model bundle has no verified artifacts")
    for name, expected in bundle["files"].items():
        h = hashlib.sha256()
        with local_path(name, roots).open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                h.update(block)
        if h.hexdigest() != expected:
            raise ValueError(f"model artifact changed: {name}")
    endpoint = urllib.parse.urlparse(bundle["endpoint"])
    if endpoint.scheme != "http" or endpoint.hostname != "127.0.0.1" or endpoint.username or endpoint.password:
        raise ValueError("model endpoint must be local loopback HTTP")
    epsilon = bundle["epsilon"]
    if type(epsilon) not in (int, float) or not math.isfinite(epsilon) or not 0 <= epsilon <= 1:
        raise ValueError("model epsilon must be in [0,1]")
    return bundle, digest(bundle)


class ModelPolicy:
    authority = "checkpoint_system_one"

    def __init__(self, seed, bundle_path, audit_path, *, transport=None):
        self.bundle, self.bundle_hash = load_policy_bundle(bundle_path)
        self.model_ref = {"bundle_sha256": self.bundle_hash, "model": self.bundle["model"]}
        self.epsilon = self.bundle["epsilon"]
        self.rng = random.Random(seed)
        self.audit_path = Path(audit_path)
        self.transport = transport or self._post
        self.context = {}

    def _audit(self, event, **fields):
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a") as out:
            out.write(canonical({"event": event, **self.context, **fields}) + "\n")
            out.flush()

    def _post(self, payload):
        request = urllib.request.Request(self.bundle["endpoint"], canonical(payload).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.bundle.get("timeout_seconds", 180)) as response:
            return json.load(response)

    def choose(self, state, menu):
        request = render(state, menu)
        request_hash = digest(request)
        self._audit("model_request", request=request, request_sha256=request_hash,
                    state_sha256=digest(state), menu_sha256=digest(menu), bundle_sha256=self.bundle_hash)
        start = time.monotonic()
        try:
            response = self.transport({"request": request, "bundle_sha256": self.bundle_hash, "request_sha256": request_hash})
            if response.get("bundle_sha256") != self.bundle_hash or response.get("request_sha256") != request_hash:
                raise ValueError("model response identity mismatch")
            if response.get("truncated") is not False or not response.get("usage") or not response.get("runtime"):
                raise ValueError("model must attest complete tokenization and runtime")
            ids = list(request["questions"][QUESTION]["criteria"])
            raw = probabilities(response["raw_probabilities"], ids)
            calibrated = probabilities(response["probabilities"], ids)
            rows = [{"option_id": o["option_id"], "need_id": o.get("need_id"), "rules": [],
                     "raw_probability": raw[o["option_id"]], "calibrated_probability": calibrated[o["option_id"]],
                     "probability": (1-self.epsilon)*calibrated[o["option_id"]]+self.epsilon/len(ids)} for o in menu["options"]]
            rng_before = self.rng.getstate()
            exploration = self.rng.random() < self.epsilon
            pick = self.rng.choice(rows) if exploration else self.rng.choices(rows, weights=[r["calibrated_probability"] for r in rows])[0]
            telemetry = {"request_sha256": request_hash, "bundle_sha256": self.bundle_hash, "renderer": RENDERER,
                         "latency_ms": (time.monotonic()-start)*1000, "usage": response["usage"],
                         "runtime": response["runtime"], "exploration": exploration, "rng_before": rng_before}
            choice = {"option_id": pick["option_id"], "need_id": pick["need_id"], "probability": pick["probability"],
                      "rules": [], "distribution": rows, "model": telemetry}
            self._audit("model_choice", choice=choice)
            return choice
        except Exception as error:
            self._audit("model_error", request_sha256=request_hash, error=f"{type(error).__name__}: {error}",
                        latency_ms=(time.monotonic()-start)*1000)
            raise
