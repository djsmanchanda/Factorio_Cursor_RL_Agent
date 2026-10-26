# Path: tools/serve_laya_model.py | Purpose: Isolate pinned decision-model runtimes behind a strict local scoring protocol.
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.metadata import version
import json
from pathlib import Path
import resource
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from training.laya_model_policy import QUESTION, canonical, digest, load_policy_bundle


def covered_windows(bounds, length):
    """Every source token must reach at least one complete-menu model pass."""
    end = 0
    for start, stop in bounds:
        if start < 0 or start > end or stop <= start or stop > length:
            raise ValueError("incomplete or invalid native input windows")
        end = max(end, stop)
    if end != length:
        raise ValueError("native input windows do not cover the whole state")


def scorer(bundle):
    import torch
    torch.set_num_threads(bundle.get("threads", 1))
    torch.set_num_interop_threads(1)
    model_path = bundle["weights"]
    kind = bundle["model"]
    runtime = {"model": kind, "python": sys.version, "torch": torch.__version__, "transformers": version("transformers"),
               "dtype": "float32", "device": "cpu", "threads": torch.get_num_threads()}

    def distributions(logits, ids, temperature):
        z = torch.as_tensor(logits, dtype=torch.float64)
        return dict(zip(ids, torch.softmax(z, -1).tolist())), dict(zip(ids, torch.softmax(z/temperature, -1).tolist()))

    if kind == "kev":
        from kev.api import SystemOneRequest, to_record
        from kev.checkpoint import Checkpoint, LoadOptions
        from kev.model import admit
        ck = Checkpoint(model_path)
        if ck.meta.base != bundle["base_repo"] or ck.meta.base_revision != bundle["base_revision"]:
            raise ValueError("Kev base identity mismatch")
        tok, model = ck.load("cpu", LoadOptions(dtype=torch.float32, backend="torch", attn="eager", merge=True, cuda_graphs=False, fused=False))
        temperature = model.head.temperature
        runtime.update(temperature=temperature, peft=version("peft"))

        def predict(body):
            rec, meta = to_record(SystemOneRequest.model_validate(body))
            enc = admit(model, tok, rec, truncate=False)
            with torch.inference_mode():
                calibrated_logits = model.forward(enc)[0]
            raw, p = distributions(calibrated_logits.double()*temperature, meta[0]["keys"], temperature)
            return raw, p, {"input_tokens": len(enc["ids"]), "state_tokens": enc["state_tokens"], "output_tokens": 0}

    elif kind == "jiwo":
        from jiwo.model import DecisionModel
        from jiwo.prompting import PromptItem
        model = DecisionModel.from_pretrained(model_path, device="cpu", dtype=torch.float32)
        temperature = model.config.temperature_for("choice")
        runtime.update(temperature=temperature)

        def predict(body):
            items = [PromptItem(body["state"], body["questions"][QUESTION])]
            ids = model.token_ids(items, max_length=8192)
            logits = model.predict_logits(items, batch_size=1, max_length=8192, ids=ids)[0]
            raw, p = distributions(logits, list(body["questions"][QUESTION]["criteria"]), temperature)
            return raw, p, {"input_tokens": len(ids[0]), "output_tokens": 0}

    elif kind == "vela":
        # Import only the pinned snapshot's runtime, never an unpinned Hub module.
        sys.path.insert(0, model_path)
        import vela2_inference as api
        engine = api.Vela2(model_path, backend="torch", device="cpu", amp_bf16=False, threads=1, batch_size=1)
        temperature = engine.temperature("choice")
        runtime.update(temperature=temperature)

        def predict(body):
            req, _ = api.system_one_to_predict(body["state"], body["questions"])
            parts, qs, _ = engine._to_generic(req)
            row = {"parts": parts, "questions": qs}
            prepped = engine._prep(row)
            rec = engine._assemble(prepped)
            ids = list(body["questions"][QUESTION]["criteria"])
            if rec["trunc"]["shrink_cut"]:
                raise ValueError("Vela would discard an unprotected input segment")
            native = engine._windows(prepped, rec) if rec["trunc"]["protected_cut"] else None
            if rec["trunc"]["protected_cut"] and not native:
                raise ValueError("Vela cannot cover the whole protected input")
            if native:
                if len(prepped["parts"]) != 1:
                    raise ValueError("native window coverage is qualified for one state part only")
                bounds = [ab for _, ab, _ in native]
                covered_windows(bounds, len(prepped["parts"][0][1]))
                passes = [engine._assemble(part) for part, _, _ in native]
            else:
                bounds, passes = [], [rec]
            for part in passes:
                if any(part["trunc"].values()) or part["questions"][0]["opt_names"] != ids:
                    raise ValueError("Vela window lost input tokens or an offered operation")
            # Released Vela averages window logits before calibration. Keep that
            # rule, running one window at a time to bound CPU memory.
            outputs = [engine._forward([part])[0]["qs"][0] for part in passes]
            logits = torch.stack([torch.as_tensor(o, dtype=torch.float64) for o in outputs]).mean(0)
            raw, p = distributions(logits[:len(ids)], ids, temperature)
            return raw, p, {"input_tokens": sum(len(part["ids"]) for part in passes), "output_tokens": 0,
                           "state_tokens": len(prepped["parts"][0][1]), "windows": bounds,
                           "window_count": len(passes), "complete_state_coverage": True,
                           "complete_menu_each_window": True, "aggregation": "mean_raw_logits_then_temperature"}
    else:
        raise ValueError("unsupported checkpoint backend")
    return predict, runtime


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle", type=Path, required=True)
    args = ap.parse_args()
    bundle, identity = load_policy_bundle(args.bundle)
    predict, runtime = scorer(bundle)
    port = int(bundle["endpoint"].split(":")[2].split("/")[0])

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.reply(200, {"ready": True, "bundle_sha256": identity, "runtime": runtime})

        def reply(self, status, result):
            body = canonical(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            started = time.monotonic()
            try:
                length = int(self.headers["Content-Length"])
                if self.path != "/score" or not 0 < length <= 4*1024*1024:
                    raise ValueError("invalid scoring request")
                payload = json.loads(self.rfile.read(length))
                request = payload["request"]
                if payload["bundle_sha256"] != identity or digest(request) != payload["request_sha256"]:
                    raise ValueError("request identity mismatch")
                if list(request["questions"]) != [QUESTION] or request["questions"][QUESTION]["type"] != "choice":
                    raise ValueError("one complete operation choice required")
                raw, p, usage = predict(request)
                self.reply(200, {"bundle_sha256": identity, "request_sha256": payload["request_sha256"],
                    "raw_probabilities": raw, "probabilities": p, "truncated": False, "usage": usage,
                    "runtime": {**runtime, "rss_max_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss},
                    "inference_ms": (time.monotonic()-started)*1000})
            except Exception as error:
                self.reply(422, {"error": f"{type(error).__name__}: {error}", "bundle_sha256": identity})

    print(canonical({"ready": True, "port": port, "bundle_sha256": identity, "runtime": runtime}), flush=True)
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
