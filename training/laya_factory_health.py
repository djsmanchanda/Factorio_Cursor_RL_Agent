# Path: training/laya_factory_health.py | Purpose: Validate scoped factory facts and render the same bounded health view for labels and live decisions.

from collections.abc import Mapping
import json
from pathlib import Path

from jsonschema import Draft7Validator

from training.contracts import _validate_finite
from training.isolation import assert_training_identity


VERSION = "workflow-health-v1"
_SCHEMA = Path(__file__).resolve().parents[1] / "schemas/laya_factory_health.schema.json"
_VALIDATOR = Draft7Validator(json.loads(_SCHEMA.read_text()))
STATES = ("working", "starved", "blocked", "unpowered", "disabled", "idle", "other")
ROLES = ("input", "output", "buffer", "held")


def validate_factory_health(health, report):
    _validate_finite(health)
    error = next(_VALIDATOR.iter_errors(health), None)
    if error:
        raise ValueError(f"invalid factory health: {error.message}")
    assert_training_identity(health["surface"], health["force"])
    tick = report["started_tick"] + report["elapsed_ticks"]
    if (health["surface"], health["force"], health["tick"]) != (report["surface"], report["force"], tick):
        raise ValueError("factory health has stale tick or foreign scope")
    for phase in health["phases"].values():
        if sum(phase["statuses"].values()) != phase["machines"] or phase["machines"] > phase["entities"]:
            raise ValueError("factory health machine counts disagree")
        if phase["working_sample_ticks"] > phase["machine_sample_ticks"]:
            raise ValueError("factory health working samples exceed machine samples")
        if max(phase["power"]["disconnected"], phase["power"]["empty"]) > phase["power"]["consumers"]:
            raise ValueError("factory health power counts disagree")
    return health


def compact_factory_health(health: Mapping) -> dict:
    """Stock remains WIP/buffer/source evidence; it is never transferable stock.

    Sampled working fraction describes status at 60-tick samples, not exact
    utilization or sustainable capacity. Unknown inventory does not become zero.
    """
    names = sorted(health["phases"])
    rows, stock, unknown = [], [], []
    for index, name in enumerate(names):
        phase = health["phases"][name]
        samples = phase["machine_sample_ticks"]
        rows.append([index, phase["entities"], phase["machines"],
                     *[phase["statuses"][state] for state in STATES],
                     *[phase["power"][key] for key in ("consumers", "disconnected", "empty")],
                     round(phase["working_sample_ticks"] / samples, 3) if samples else None])
        for role in ROLES:
            inventory = phase["stock"][role]
            if not inventory["available"]:
                unknown.append([index, role])
            else:
                stock.extend([index, role, item, count] for item, count in sorted(inventory["items"].items()))
    return {
        "version": VERSION, "tick": health["tick"], "phase_names": names,
        "phase_columns": ["phase", "entities", "machines", *STATES, "consumers", "disconnected", "empty_power", "sampled_working_fraction"],
        "phases": rows,
        "stock_columns": ["phase", "role", "item", "count"], "stock": stock,
        "stock_scope": health["stock_scope"], "unknown_stock": unknown,
        "resource_remaining": health["resource_remaining"],
        "construction_remaining": dict(sorted(health["construction_remaining"].items())),
        "construction_budget_kind": "entity_allowance",
        "source_stock": [[source, item, count] for source, inventory in sorted(health["sources"].items())
                         if inventory["available"] for item, count in sorted(inventory["items"].items())],
        "unknown_sources": [source for source, inventory in sorted(health["sources"].items()) if not inventory["available"]],
    }
