# Path: training/laya_saved_counterfactual.py | Purpose: Completion-first labels from matched saved-world branches, keeping one save lineage in one split.

from datetime import datetime, timezone

from training.laya_counterfactual import preference_target
from training.laya_saved_world import SavedWorldError, decision_state
from training.laya_workflow import QUESTION_INSTRUCTIONS
from orchestrator.laya_operations import sha256_hash
from training.laya_dataset import validate_case


def replay_fingerprint(facts):
    """Runtime network IDs may change on load; physical ports define identity."""
    networks = {}
    for key, value in facts["networks"].items():
        ports = sorted((p["position"]["x"], p["position"]["y"]) for p in facts["ports"] if p["network"] == key)
        networks[str(ports)] = value
    ports = [{k: v for k, v in p.items() if k != "network"} for p in facts["ports"]]
    poles = [{k: v for k, v in p.items() if k != "grid"} for p in facts.get("poles", [])]
    grids = {}
    for p in facts.get("poles", []):
        grids.setdefault(p.get("grid"), []).append((p["position"]["x"], p["position"]["y"]))
    return sha256_hash({"tick": facts["tick"], "networks": networks,
        "surface": facts.get("surface"), "force": facts.get("force"),
        "ports": sorted(ports, key=lambda p: (p["position"]["x"], p["position"]["y"])),
        "poles": sorted(poles, key=lambda p: (p["position"]["x"], p["position"]["y"])),
        "grids": sorted(sorted(positions) for positions in grids.values()),
        "resources": sorted(facts.get("resources", []), key=lambda r: (r["item"], r["position"]["x"], r["position"]["y"])),
        "recipes": facts.get("recipes"), "researched": facts.get("researched"),
        "machines": sorted(facts["machines"], key=lambda m: (m["name"], m["position"]["x"], m["position"]["y"])),
        "production": facts["production"], "consumption": facts["consumption"],
        "pending_ghosts": facts["pending_ghosts"], "player_stock": facts["player_stock"],
        "infrastructure": facts["infrastructure_counts"], "mods": facts["mods"]})


def assess(samples, goal, rate, expected_windows):
    if len(samples) != expected_windows or any(s["window_ticks"] != 300 for s in samples):
        raise SavedWorldError("incomplete common sustain window")
    complete = all(s["production"][goal]["per_second"] >= rate and s["pending_ghosts"] == 0 for s in samples)
    return {"status": "completed" if complete else "timed_out", "failure_cause": "none" if complete else "policy",
            "failure_kind": "none" if complete else "timeout", "safety_violations": 0,
            "rates": [s["production"][goal]["per_second"] for s in samples]}


def label_to_case(label):
    expected = {c["option_id"] for c in label["candidates"]}
    if expected != set(label["outcomes"]) or expected != set(label["costs"]):
        return None, "missing alternatives"
    fingerprints = {o.get("replay_fingerprint") for o in label["outcomes"].values()}
    if fingerprints != {label["start_fingerprint"]}:
        return None, "mismatched starting states"
    clocks = {o.get("horizon_ticks") for o in label["outcomes"].values()}
    if clocks != {label["horizon_ticks"]}:
        return None, "mismatched replay horizons"
    probabilities, weight, reason = preference_target(label["costs"], label["outcomes"], 120, 0)
    if probabilities is None:
        return None, reason
    evidence = sha256_hash(label)
    ancestor = label["ancestor_source_sha256"]
    # Menu randomization is not an independent Factorio world. All prefix
    # siblings retain the same ancestry-derived scenario seed and split.
    scenario_seed = int(ancestor[:8], 16) & 0x7fffffff
    model = {"model_id": "measured-counterfactual", "model_hash": sha256_hash({"method": "saved-world-cf-v1"})}
    description = f"Sustain {label['goal']} production at {label['rate_per_second']}/s for {label['sustain_ticks']} ticks."
    state = decision_state(label["facts"], label["candidates"], label["run_id"], model, description)
    state["objective"].update(target=label["rate_per_second"])
    state["acceptance_gap"].update(metric=label["goal"], target=label["rate_per_second"], required_sustained_ticks=label["sustain_ticks"])
    case = {"version": "1.0.0", "case_id": "swcf." + evidence[7:31],
        "source": {"kind": "rl_transition", "run_id": label["run_id"], "episode_id": "saved-world." + ancestor[:16],
            "scenario_id": "saved-world." + ancestor[:16], "scenario_seed": scenario_seed,
            "scenario_hash": "sha256:" + ancestor, "provenance_hash": evidence},
        "state": state, "questions": {"select_candidate": {"type": "choice", "instructions": QUESTION_INSTRUCTIONS["select_candidate"],
            "criteria": {c["option_id"]: c["summary"] for c in label["candidates"]}}},
        "gold": {"select_candidate": {"label": max(probabilities, key=probabilities.get), "probabilities": probabilities,
            "label_source": "counterfactual", "confidence": weight}},
        "weights": {"case_weight": weight, "question_weights": {"select_candidate": weight}, "source_quality": 1.0},
        "created_at": datetime.now(timezone.utc).isoformat(), "dataset_version": "1.0.0", "split": label["split"],
        "label_provenance": {"method": "counterfactual", "evidence_hash": evidence,
            "notes": "saved-world-cf-v1; engine counters; common exact horizon; same paused save; completion-first; diagnostic-only"}}
    validate_case(case)
    return case, ""
