# Path: core/optimizer_consumer_window.py | Purpose: Compare scoped consumer counter observations without claiming fluid delivery.
"""Offline comparison of caller-supplied reports; no runtime authentication or commissioning."""

import hashlib
import json
import math
from pathlib import Path

from jsonschema import Draft7Validator

from core.optimizer_artifact_binding import BoundArtifactPlan, bind_artifact_to_construction_plan


def compare_consumer_window(bound, start, end, *, expected_recipe):
    """Compare a single unit's engine counter at two ticks, without converting it to fluid.

    Matching endpoint recipes do not prove that a recipe stayed unchanged in the
    intervening period. A positive counter delta is reported as an observation,
    never as delivery, consumption, capacity, ownership, or commissioning.
    """
    if not isinstance(bound, BoundArtifactPlan) or not isinstance(expected_recipe, str) or not expected_recipe:
        raise ValueError("bound optimizer artifact and expected consumer recipe are required")
    plan, binding = bound.plan, bound.plan.binding
    rebound = bind_artifact_to_construction_plan(
        bound.artifact, scope=binding.scope, project_id=binding.project_id, transaction_id=binding.transaction_id,
        throughput_output=plan.throughput_output, throughput_target=plan.throughput_target,
        throughput_unit=plan.throughput_unit, expected_artifact_sha256=bound.artifact_sha256,
        expected_plan_hash=bound.binding_sha256)
    if rebound.plan != plan or rebound.canonical_binding != bound.canonical_binding:
        raise ValueError("optimizer artifact binding is inconsistent")
    schema = json.loads((Path(__file__).resolve().parents[1] / "schemas/optimizer_consumer_observation.schema.json").read_text())
    validator = Draft7Validator(schema)
    if (not isinstance(start, dict) or not isinstance(end, dict) or not validator.is_valid(start)
            or not validator.is_valid(end) or start.get("ok") is not True or end.get("ok") is not True):
        raise ValueError("consumer observation report is invalid")
    for report in (start, end):
        requested = report["requested"]
        pos = requested["position"]
        if (type(report["tick"]) is not int or type(requested["unit_number"]) is not int
                or any(type(value) not in (int, float) or abs(value) > 1_000_000
                       or not math.isfinite(value) for value in pos.values())):
            raise ValueError("consumer observation has invalid numeric identity or tick")
        observation = report["observation"]
        if (observation["state"] == "present" and observation["products_finished"]["state"] == "available"
                and type(observation["products_finished"]["value"]) is not int):
            raise ValueError("consumer observation counter must be an integer")
    if (start["request_id"] == end["request_id"] or end["tick"] <= start["tick"]
            or start["surface"] != binding.scope.surface or start["force"] != binding.scope.force
            or any(start[key] != end[key] for key in ("surface", "force", "requested"))):
        raise ValueError("consumer observation scope, identity, or window does not match")
    canonical = json.dumps({"binding": binding.to_dict(), "start": start, "end": end, "recipe": expected_recipe},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    result = {"version": "optimizer-consumer-window-v1", "evidence_sha256": hashlib.sha256(canonical).hexdigest(),
              "binding": binding.to_dict(), "source_output": plan.throughput_output, "consumer": start["requested"],
              "expected_recipe": expected_recipe, "start_tick": start["tick"], "end_tick": end["tick"],
              "status": "unverified", "reason": None, "counter_delta": None,
              "ownership_claimed": False, "commissioned": False, "dispatch_authorized": False,
              "non_claims": ["reports and scope are caller assertions, not authenticated runtime evidence",
                             "endpoint recipe matches do not establish uninterrupted recipe or entity continuity",
                             "counter advances are not converted to fluid consumption or delivery",
                             "the consumer recipe and unit are not proven connected to the optimizer output",
                             "no ownership, commissioning, reservation release or dispatch authority is granted"]}
    a, b = start["observation"], end["observation"]
    if a["state"] != "present" or b["state"] != "present":
        result["reason"] = "consumer_unavailable"
    elif any(row["recipe"].get("state") != "available" or row["recipe"].get("value") != expected_recipe
             for row in (a, b)):
        result["reason"] = "recipe_unavailable_or_changed"
    elif any(row["products_finished"].get("state") != "available" for row in (a, b)):
        result["reason"] = "counter_unavailable"
    else:
        first, last = a["products_finished"]["value"], b["products_finished"]["value"]
        if last < first:
            result["reason"] = "counter_regressed"
        else:
            result.update(status="counter_advanced" if last > first else "no_counter_advance",
                          counter_delta=last - first)
    return result
