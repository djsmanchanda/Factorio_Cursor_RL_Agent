# Path: tests/test_optimizer_fluid_topology.py | Purpose: Check that fluid topology diagnostics use the bound planner intent.
from dataclasses import replace
from fractions import Fraction
import copy

import pytest

from core.fluid_topology_intent import fluid_topology_intent
from core.optimizer_artifact_binding import ArtifactBindingError, bind_artifact_to_construction_plan
from core.optimizer_construction_state import ConstructionScope
from planners.oil_block_artifact import build_oil_block_artifact
from tests.test_fluid_topology import built, codes, load, snapshot
from tests.test_oil_block_artifact import oil_arguments
from tools.optimizer_fluid_topology import check_bound_fluid_topology


@pytest.fixture(scope="module")
def candidate(oil_arguments):
    return build_oil_block_artifact(**oil_arguments)


def bind(artifact, *, scope=None, expected=None):
    return bind_artifact_to_construction_plan(
        artifact, scope=scope or ConstructionScope("synthetic", "a" * 64, "nauvis", "player"),
        project_id="oil", transaction_id="a" * 32, throughput_output="petroleum-gas",
        throughput_target=Fraction(195), throughput_unit="fluid/second", expected_plan_hash=expected)


def diagnostic(bound, **kwargs):
    return check_bound_fluid_topology(bound, catalog=load(), snapshot=kwargs.get("snapshot", snapshot()),
                                      reference_tick=1010, max_snapshot_age_ticks=60)


def test_oil_planner_binds_segments_and_adapter_checks_real_geometry(candidate):
    artifact = candidate["artifact"]
    bound = bind(artifact)
    intent = bound.artifact["planning_context"]["data"]["fluid_topology_intent"]
    assert intent == fluid_topology_intent(artifact["plan"], candidate["segments"])
    result = diagnostic(bound)
    assert result["status"] == "unverified" and result["conflicts"] == []
    assert codes(result, "unverified") == {"underground_tile_blocking_unverified"}
    assert result["underground_pairs"] == 92 and result["dispatch_authorized"] is False
    assert result["binding"] == bound.plan.binding.to_dict() and result["intent_trust"] == "unverified"
    changed = copy.deepcopy(artifact)
    changed["planning_context"]["data"]["fluid_topology_intent"]["segments"][0]["fluid"] = "water"
    with pytest.raises(ArtifactBindingError): bind(changed, expected=bound.binding_sha256)


def test_attachment_intent_is_bound_and_requires_observed_matching_source(oil_arguments, candidate):
    port = candidate["ports"]["crude-oil"][0]
    (ex, ey), (ax, ay) = port["west"], port["attach"]
    source = built("pipe", ax + .5, ay + .5, fluid="crude-oil", unit=99)
    permit = {"planned": {"entity": "pipe", "position": {"x": ex + .5, "y": ey + .5}},
              "existing": {"entity": "pipe", "position": source["position"], "unit_number": 99},
              "fluid": "crude-oil"}
    artifact = build_oil_block_artifact(**oil_arguments, permitted_fluid_attachments=[permit])["artifact"]
    bound = bind(artifact)
    assert bound.artifact["planning_context"]["data"]["fluid_topology_intent"]["permitted_attachments"] == [permit]
    assert diagnostic(bound, snapshot=snapshot([source]))["conflicts"] == []
    source["fluidboxes"][0]["segment_fluid"] = "water"
    assert "attachment_fluid_mismatch" in codes(diagnostic(bound, snapshot=snapshot([source])))
    changed = copy.deepcopy(artifact)
    changed["planning_context"]["data"]["fluid_topology_intent"]["permitted_attachments"][0]["existing"]["unit_number"] = 100
    with pytest.raises(ArtifactBindingError): bind(changed, expected=bound.binding_sha256)


def test_adapter_rejects_legacy_missing_intent_and_altered_binding(candidate):
    legacy = copy.deepcopy(candidate["artifact"])
    legacy["planning_context"]["data"].pop("fluid_topology_intent")
    with pytest.raises(ValueError, match="lacks explicit"):
        diagnostic(bind(legacy))
    bound = bind(candidate["artifact"])
    with pytest.raises(ValueError, match="inconsistent"):
        diagnostic(replace(bound, canonical_binding=bound.canonical_binding + b" "))
    foreign = bind(candidate["artifact"], scope=ConstructionScope("x", "a" * 64, "elsewhere", "player"))
    with pytest.raises(ValueError, match="scope differs"):
        diagnostic(foreign)


@pytest.mark.parametrize("change", ["unit", "planned", "fluid", "duplicate", "endpoint", "tile"])
def test_intent_rejects_malformed_or_unbound_attachments(candidate, change):
    plan = candidate["artifact"]["plan"]
    segments = candidate["segments"]
    port = candidate["ports"]["crude-oil"][0]
    (ex, ey), (ax, ay) = port["west"], port["attach"]
    permit = {"planned": {"entity": "pipe", "position": {"x": ex + .5, "y": ey + .5}},
              "existing": {"entity": "pipe", "position": {"x": ax + .5, "y": ay + .5}, "unit_number": 99},
              "fluid": "crude-oil"}
    permits = [permit]
    if change == "unit": permit["existing"]["unit_number"] = True
    elif change == "planned": permit["planned"]["position"]["x"] += 1000
    elif change == "fluid": permit["fluid"] = "water"
    elif change == "duplicate": permits.append(copy.deepcopy(permit))
    else:
        segments = copy.deepcopy(segments)
        if change == "tile": segments[0]["tiles"].append((999999, 999999))
        else: segments[0]["tunnel_endpoints"][0] = ((999999, 999999), (999998, 999998))
    with pytest.raises(ValueError): fluid_topology_intent(plan, segments, permits)
