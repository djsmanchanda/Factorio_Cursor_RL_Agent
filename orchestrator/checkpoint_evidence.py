# Path: orchestrator/checkpoint_evidence.py | Purpose: Project retained checkpoint observations without changing historical predicates.
"""Versioned, bounded observation evidence; this is not a milestone latch."""
from __future__ import annotations

from dataclasses import replace

from orchestrator.checkpoint_milestones import (
    C6_LOGISTICS_CHESTS, DEFAULT_MILESTONES, LATER_MILESTONE_IDS, c5_ledger, c5_ledger_or_none, evaluate_milestone,
    _plastic_sample,
)

#: Strict history bound shared by the runner session, the sidecar and restart bundles.
HISTORY_LIMIT = 20


def retained_samples(history):
    """An identical paused observation adds no evidence or sampling boundary."""
    samples = list(history)
    return [sample for i, sample in enumerate(samples) if i == 0 or sample != samples[i - 1]]


def bounded_history(history, limit=HISTORY_LIMIT):
    """The newest ``limit`` retained samples; the oldest carries C5's ledger of every sample trimmed before it.

    C5 is historical and measured over settled windows of any sampling density, so the samples it needs do
    not fit in a fixed number of snapshots. Its ledger (``checkpoint_milestones.c5_ledger``) is the C5 fold
    over the trimmed samples, continued from the ledger the previous oldest sample carried, so C5 over the
    bounded history equals C5 over the full one. Every other predicate sees the newest samples, as before.
    """
    samples = retained_samples(history)
    if len(samples) <= limit:
        return samples
    trimmed = len(samples) - limit
    ledger = c5_ledger_or_none(c5_ledger(samples[:trimmed]))
    return [replace(samples[trimmed], c5_ledger=ledger), *samples[trimmed + 1:]]


def project_evidence(baseline, history):
    samples = retained_samples(history)
    if not samples or baseline is None:
        return {"version": "checkpoint-evidence-v1", "present": None, "inherited_start": None,
                "observed_events": [], "proxy_phase": None, "coverage": "unknown"}
    def results(sample, prefix):
        return {m.checkpoint_id: {"version": m.predicate_version,
                "status": evaluate_milestone(m.checkpoint_id, sample, baseline=baseline, history=prefix).status.value}
                for m in DEFAULT_MILESTONES}
    present = results(samples[-1], samples)
    inherited = results(baseline, [baseline])
    events = []
    previous = None
    for i, sample in enumerate(samples):
        current = results(sample, samples[:i + 1])
        if (previous is not None and samples[i-1].tick is not None and sample.tick is not None
                and sample.tick > samples[i-1].tick):
            for checkpoint in current:
                if current[checkpoint]["status"] == "passed" and previous[checkpoint]["status"] == "failed":
                    events.append({"kind": "predicate_transition", "checkpoint": checkpoint,
                                   "predicate_version": current[checkpoint]["version"],
                                   "from_tick": samples[i-1].tick, "tick": sample.tick,
                                   "provenance": "adjacent_retained_observations"})
        previous = current
    plastic = []
    consumers = []
    for before, after in zip(samples, samples[1:]):
        t0, t1 = before.tick, after.tick
        if t0 is None or t1 is None or t1 <= t0:
            continue
        p0, p1 = _plastic_sample(before), _plastic_sample(after)
        plastic.append({"from_tick": t0, "tick": t1,
                        "craft_delta": None if p0[0] is None or p1[0] is None else p1[0]-p0[0],
                        "delivery_delta": None if p0[1] is None or p1[1] is None else p1[1]-p0[1]})
        if not isinstance(before.ac_consumers, (list, tuple)) or not isinstance(after.ac_consumers, (list, tuple)):
            continue
        old = {row.get("unit"): row for row in before.ac_consumers if isinstance(row, dict) and row.get("unit") is not None}
        for row in after.ac_consumers:
            if (not isinstance(row, dict) or type(row.get("unit")) is not int
                    or row["unit"] <= 0 or not isinstance(row.get("recipe"), str)
                    or not row["recipe"] or row["recipe"] in C6_LOGISTICS_CHESTS):
                continue
            prior = old.get(row["unit"])
            if not prior or prior.get("recipe") != row["recipe"]:
                continue
            a, b = prior.get("count"), row.get("count")
            if type(a) is int and type(b) is int and 0 <= a < b:
                consumers.append({"unit": row["unit"], "recipe": row["recipe"], "from_count": a,
                                  "count": b, "from_tick": t0, "tick": t1,
                                  "kind": "productivity_module" if row["recipe"] == "productivity-module" else "generic_consumer",
                                  "provenance": "adjacent_retained_observations"})
    return {"version": "checkpoint-evidence-v1", "present": present,
            "inherited_start": {"tick": baseline.tick, "predicates": inherited,
                                "provenance": "first_observation_not_earned"},
            "observed_events": events, "proxy_phase": None,
            "coverage": {"retained_samples": len(samples), "first_tick": samples[0].tick,
                         "last_tick": samples[-1].tick, "complete_history": False},
            "sampled_plastic": {"version": "sampled-plastic-v1", "intervals": plastic,
                                "interpretation": "sampled_deltas_only_no_continuity_claim"},
            "consumer_events": consumers, "research": {"C7": present["C7"], "C8": present["C8"], "C9": present["C9"]}}

# The accumulator keeps first events and bounded recent generation boundaries, not snapshots.
SAMPLING_CONTRACT = "checkpoint-sampling-v2-adjacent-exact-dedup"
MAX_BOUNDARIES = 100
MAX_CONSUMERS = 256


def new_evidence_state(*, origin="observed"):
    return {"version": 2, "origin": origin, "inherited_start": None, "events": {},
            "consumer_events": {}, "consumers": {}, "generations": {}, "boundaries": [], "boundaries_dropped": 0,
            "failed_polls": 0, "gap": origin != "observed", "current_available": False,
            "last_tick": None, "last_statuses": None, "consumer_telemetry": False}


def _known_ids(statuses):
    """Every current milestone id, except that a state older than a later milestone may lack it."""
    ids = {m.checkpoint_id for m in DEFAULT_MILESTONES}
    return ids - LATER_MILESTONE_IDS <= set(statuses) <= ids


def validate_evidence_state(state):
    import json
    template = new_evidence_state()
    if not isinstance(state, dict) or set(state) != set(template) or state.get("version") != 2:
        raise ValueError("Invalid checkpoint evidence state")
    if state["origin"] not in ("observed", "unknown") or any(type(state[k]) is not bool for k in ("gap", "current_available", "consumer_telemetry")):
        raise ValueError("Invalid checkpoint evidence origin or flags")
    if any(type(state[k]) is not int or state[k] < 0 for k in ("failed_polls", "boundaries_dropped")):
        raise ValueError("Invalid checkpoint evidence counts")
    if state["last_tick"] is not None and type(state["last_tick"]) is not int:
        raise ValueError("Invalid checkpoint evidence tick")
    if not isinstance(state["boundaries"], list) or len(state["boundaries"]) > MAX_BOUNDARIES:
        raise ValueError("Invalid checkpoint evidence boundaries")
    if any(not isinstance(state[k], dict) for k in ("events", "consumer_events", "consumers", "generations")) or len(state["generations"]) > MAX_CONSUMERS or len(state["consumers"]) > MAX_CONSUMERS or len(state["consumer_events"]) > MAX_CONSUMERS:
        raise ValueError("Invalid checkpoint evidence records")
    if not set(state["events"]) <= {m.checkpoint_id for m in DEFAULT_MILESTONES}:
        raise ValueError("Invalid checkpoint evidence event")
    statuses = state["last_statuses"]
    if statuses is not None and (not isinstance(statuses, dict) or not _known_ids(statuses) or any(v not in ("passed", "failed", "unknown") for v in statuses.values())):
        raise ValueError("Invalid checkpoint evidence statuses")
    if any(not isinstance(k, str) or not k.isdecimal() or type(v) is not int or v < 0 for k, v in state["generations"].items()):
        raise ValueError("Invalid checkpoint generations")
    for key, row in state["consumers"].items():
        if not isinstance(key, str) or not key.isdecimal() or not isinstance(row, dict) or set(row) != {"recipe", "count", "generation"} or not isinstance(row["recipe"], str) or type(row["count"]) is not int or row["count"] < 0 or type(row["generation"]) is not int or row["generation"] < 0:
            raise ValueError("Invalid checkpoint consumer state")
    for entry in state["boundaries"]:
        if (not isinstance(entry, dict) or set(entry) != {"reason", "tick", "unit"}
                or entry["reason"] not in {"failed_poll", "missing_consumer_telemetry", "removal", "appearance", "gap", "retarget", "reset", "clock_discontinuity"}
                or (entry["tick"] is not None and type(entry["tick"]) is not int)
                or (entry["unit"] is not None and (type(entry["unit"]) is not int or entry["unit"] <= 0))):
            raise ValueError("Invalid checkpoint boundary")
    for cid, event in state["events"].items():
        if (not isinstance(event, dict) or set(event) != {"kind", "checkpoint", "predicate_version", "from_tick", "tick", "provenance"}
                or event["checkpoint"] != cid or event["kind"] != "predicate_transition"
                or event["provenance"] != "adjacent_successful_observations"
                or not isinstance(event["predicate_version"], str) or type(event["from_tick"]) is not int
                or type(event["tick"]) is not int or event["tick"] <= event["from_tick"]):
            raise ValueError("Invalid checkpoint event")
    for identity, event in state["consumer_events"].items():
        if (not isinstance(identity, str) or not isinstance(event, dict)
                or set(event) != {"unit", "recipe", "generation", "from_count", "count", "from_tick", "tick", "kind", "provenance"}
                or type(event["unit"]) is not int or event["unit"] <= 0
                or not isinstance(event["recipe"], str) or not event["recipe"] or event["recipe"] in C6_LOGISTICS_CHESTS
                or any(type(event[k]) is not int for k in ("generation", "from_count", "count", "from_tick", "tick"))
                or identity != f"{event['unit']}:{event['generation']}"
                or event["generation"] < 0 or not 0 <= event["from_count"] < event["count"] or event["tick"] <= event["from_tick"]
                or event["kind"] != ("productivity_module" if event["recipe"] == "productivity-module" else "generic_consumer")
                or event["provenance"] != "sampled_delta_unseen_retargets_unknown"):
            raise ValueError("Invalid consumer event")
    inherited = state["inherited_start"]
    if inherited is not None and (not isinstance(inherited, dict) or set(inherited) != {"tick", "predicates", "provenance"}
            or inherited["provenance"] != "first_observation_not_earned" or not isinstance(inherited["predicates"], dict)
            or not _known_ids(inherited["predicates"])
            or any(not isinstance(v, dict) or set(v) != {"version", "status"} or not isinstance(v["version"], str)
                   or v["status"] not in ("passed", "failed", "unknown") for v in inherited["predicates"].values())
            or (inherited["tick"] is not None and type(inherited["tick"]) is not int)):
        raise ValueError("Invalid inherited evidence")
    try:
        json.dumps(state, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid checkpoint evidence JSON") from error
    return state


def _boundary(state, reason, tick, unit=None):
    state["boundaries"].append({"reason": reason, "tick": tick, "unit": unit})
    if len(state["boundaries"]) > MAX_BOUNDARIES:
        state["boundaries"].pop(0)
        state["boundaries_dropped"] += 1


def evidence_failure(state):
    state["failed_polls"] += 1
    state["gap"] = True
    state["current_available"] = False
    _boundary(state, "failed_poll", state["last_tick"])


def evidence_success(state, baseline, history):
    samples = retained_samples(history)
    sample = samples[-1]
    projection = project_evidence(baseline, samples)
    statuses = {k: v["status"] for k, v in projection["present"].items()}
    if state["inherited_start"] is None and state["origin"] == "observed":
        state["inherited_start"] = projection["inherited_start"]
    previous = state["last_statuses"]
    tick = sample.tick
    if type(tick) is not int or (state["last_tick"] is not None and tick < state["last_tick"]):
        _boundary(state, "clock_discontinuity", tick)
        state["gap"] = True
    advancing = type(tick) is int and type(state["last_tick"]) is int and tick > state["last_tick"]
    if previous is not None and advancing and not state["gap"]:
        for cid, status in statuses.items():
            # An event recorded under an older predicate version (a restart bundle's c5-v1) is
            # superseded by the first transition under the current version.
            recorded = state["events"].get(cid)
            if (status == "passed" and previous.get(cid) == "failed"
                    and (recorded is None or recorded["predicate_version"] != projection["present"][cid]["version"])):
                state["events"][cid] = {"kind": "predicate_transition", "checkpoint": cid,
                    "predicate_version": projection["present"][cid]["version"], "from_tick": state["last_tick"],
                    "tick": tick, "provenance": "adjacent_successful_observations"}
    telemetry = isinstance(sample.ac_consumers, (list, tuple))
    rows = {}
    if telemetry:
        for row in sample.ac_consumers:
            if (isinstance(row, dict) and type(row.get("unit")) is int and row["unit"] > 0
                    and isinstance(row.get("recipe"), str) and row["recipe"] and row["recipe"] not in C6_LOGISTICS_CHESTS
                    and type(row.get("count")) is int and row["count"] >= 0):
                rows[str(row["unit"])] = row
    old = state["consumers"]
    if not telemetry:
        if state["consumer_telemetry"]:
            _boundary(state, "missing_consumer_telemetry", tick)
        old = {}
    else:
        for unit in old.keys() - rows.keys():
            _boundary(state, "removal", tick, int(unit))
        updated = {}
        for unit, row in sorted(rows.items())[:MAX_CONSUMERS]:
            prior = old.get(unit)
            reason = None
            if prior is None:
                reason = "appearance"
            elif state["gap"] or not state["consumer_telemetry"]:
                reason = "gap"
            elif prior["recipe"] != row["recipe"]:
                reason = "retarget"
            elif row["count"] < prior["count"]:
                reason = "reset"
            generation = (prior["generation"] + 1 if prior and reason else prior["generation"] if prior
                          else state["generations"].get(unit, -1) + 1)
            if unit not in state["generations"] and len(state["generations"]) >= MAX_CONSUMERS:
                del state["generations"][next(iter(state["generations"]))]
            state["generations"][unit] = generation
            if reason:
                _boundary(state, reason, tick, int(unit))
            elif (advancing and row["count"] > prior["count"]
                  and f"{unit}:{generation}" not in state["consumer_events"]
                  and len(state["consumer_events"]) < MAX_CONSUMERS):
                state["consumer_events"][f"{unit}:{generation}"] = {"unit": int(unit), "recipe": row["recipe"], "generation": generation,
                    "from_count": prior["count"], "count": row["count"], "from_tick": state["last_tick"], "tick": tick,
                    "kind": "productivity_module" if row["recipe"] == "productivity-module" else "generic_consumer",
                    "provenance": "sampled_delta_unseen_retargets_unknown"}
            updated[unit] = {"recipe": row["recipe"], "count": row["count"], "generation": generation}
        old = updated
    state.update(consumers=old, consumer_telemetry=telemetry, last_tick=tick, last_statuses=statuses,
                 gap=False, current_available=True)


def state_projection(baseline, history, state, proxy_phase=None):
    result = project_evidence(baseline, history)
    result.update(sampling_contract=SAMPLING_CONTRACT, proxy_phase=proxy_phase,
                  inherited_start=state["inherited_start"], observed_events=list(state["events"].values()),
                  consumer_events=list(state["consumer_events"].values()),
                  generation_boundaries=list(state["boundaries"]), boundaries_dropped=state["boundaries_dropped"],
                  failed_polls=state["failed_polls"], origin=state["origin"],
                  consumer_state=state["consumers"], current_available=state["current_available"])
    if not state["current_available"]:
        result["present"] = None
        result["research"] = None
    return result
