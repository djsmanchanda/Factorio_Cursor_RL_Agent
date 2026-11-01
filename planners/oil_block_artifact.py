# Path: planners/oil_block_artifact.py | Purpose: Assemble an offline, explicitly routed oil balance into a ghost artifact.
"""Build an oil-row candidate, not a deployable or runtime-verified factory.

The caller chooses every row location, orientation, trunk and routing order.
Nothing here surveys terrain, supplies inputs, powers machines, verifies
world underground pairing or pipeline capacity, or authorizes execution.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json

from core.bounded_fluid_machines import compute_bounded_fluid_machines
from core.fluid_systems import validate_network_purity, validate_pipeline_span
from core.fluid_topology_intent import fluid_topology_intent
from core.optimizer_artifact_binding import bind_artifact_to_construction_plan
from core.optimizer_construction_state import FLUID_PER_SECOND, ConstructionScope
from planners.fluid_layout_search import (
    oriented_fluid_machine_row, oriented_fluid_network_segments,
    oriented_header_attachment,
)
from planners.fluid_routing import fluid_chain_link_segments, generate_fluid_chain_link
from planners.fluid_underground import validate_planned_underground_pairs
from planners.ghost_line_artifact import derive_ghost_artifact, derive_ghost_artifact_from_runtime_catalog
from planners.infrastructure import strip_local_power
from planners.plan_validation import (
    actions, assert_no_production_infinity, entity_footprint_tiles, validate_build_plan,
)

_OIL_RECIPES = frozenset({
    "basic-oil-processing", "advanced-oil-processing",
    "heavy-oil-cracking", "light-oil-cracking",
})
_MAX_MACHINES = 64
_MAX_SPAN = 512


def _check_capture_catalog_consistency(catalog: object, capture: object) -> None:
    """Check claimed force/mod agreement, not common server or save identity."""
    if not isinstance(catalog, Mapping) or not isinstance(capture, Mapping):
        raise ValueError("capture-backed oil planning requires recipe catalog provenance")
    mods = catalog.get("active_mods")
    if (catalog.get("version") != "1.3.0" or not isinstance(mods, Mapping) or not mods
            or any(not isinstance(name, str) or not name or not isinstance(version, str) or not version
                   for name, version in mods.items())
            or type(catalog.get("active_mods_count")) is not int
            or catalog["active_mods_count"] != len(mods)
            or not isinstance(catalog.get("force"), str) or not catalog["force"]
            or catalog["force"] != capture.get("force")
            or not isinstance(capture.get("active_mods"), Mapping)
            or dict(mods) != dict(capture["active_mods"])):
        raise ValueError("recipe and placement capture force/mod provenance mismatch")


def _integer(value: object, label: str) -> int:
    if type(value) is not int or abs(value) > 1_000_000:
        raise ValueError(f"{label} must be an integer within the supported coordinate bound")
    return value


def _check_fluid_overlap(segments: list[dict]) -> None:
    owners: dict[tuple[int, int], str] = {}
    for segment in segments:
        for tile in segment["tiles"]:
            position = tuple(tile)
            if position in owners and owners[position] != segment["fluid"]:
                raise ValueError(f"different fluids share tile {position}")
            owners[position] = segment["fluid"]
    validate_network_purity(segments)


def _check_spans(segments: list[dict], machine_tiles: Mapping[str, set[tuple[int, int]]]) -> dict:
    spans = {}
    for fluid in sorted(machine_tiles):
        tiles = set(machine_tiles[fluid])
        for segment in segments:
            if segment["fluid"] == fluid:
                tiles.update(map(tuple, segment["tiles"]))
        validate_pipeline_span(list(tiles))
        xs, ys = zip(*tiles)
        spans[fluid] = {"width": max(xs) - min(xs), "height": max(ys) - min(ys)}
    return spans


def build_oil_block_artifact(
    catalog: object, *, balance: object, machine_speeds: object,
    rows: Mapping[str, Mapping[str, object]], trunk_columns: Mapping[str, int],
    route_order: Sequence[str], item_mapping: Mapping[str, object] | None = None,
    placement_capture: Mapping[str, object] | None = None,
    max_machines: int, permitted_fluid_attachments: Sequence[Mapping[str, object]] = (),
) -> dict:
    """Re-verify a balance and assemble explicitly situated oil rows and links.

    Each row specifies integer ``origin`` (tile coordinates), ``direction`` and
    ``mirror``. Every fluid present on multiple rows needs one trunk column,
    and ``route_order`` must name each such fluid exactly once. A single-row
    header remains an open attachment, not a connected source or destination.
    Supply exactly one caller mapping or pinned runtime placement capture.
    Both are untrusted assertions, not authorization or evidence that any
    route works in a particular world.
    """
    if (item_mapping is None) == (placement_capture is None):
        raise ValueError("provide exactly one item_mapping or placement_capture")
    if placement_capture is not None:
        _check_capture_catalog_consistency(catalog, placement_capture)
    limit = _integer(max_machines, "max_machines")
    if not 1 <= limit <= _MAX_MACHINES:
        raise ValueError(f"max_machines must be 1..{_MAX_MACHINES}")
    sizing = compute_bounded_fluid_machines(
        catalog, balance=balance, machine_speeds=machine_speeds,
    )
    facts = {fact.recipe: fact for fact in sizing.facts}
    if not facts or not set(facts) <= _OIL_RECIPES:
        raise ValueError("only oil processing and cracking rows are supported")
    if sizing.total_machines > limit:
        raise ValueError("machine count exceeds the caller's bound")
    if not isinstance(rows, Mapping) or set(rows) != set(facts):
        raise ValueError("rows must specify exactly the selected recipes")

    plans: list[dict] = []
    segments: list[dict] = []
    ports: dict[str, list[dict]] = {}
    machine_tiles: dict[str, set[tuple[int, int]]] = {}
    coordinates: list[tuple[int, int]] = []
    for recipe in sorted(facts):
        row = rows[recipe]
        if not isinstance(row, Mapping) or set(row) != {"origin", "direction", "mirror"}:
            raise ValueError(f"row {recipe} requires origin, direction and mirror")
        origin = row["origin"]
        if not isinstance(origin, (tuple, list)) or len(origin) != 2:
            raise ValueError(f"row {recipe} origin must have two integer coordinates")
        origin = (_integer(origin[0], "origin x"), _integer(origin[1], "origin y"))
        coordinates.append(origin)
        direction, mirror = row["direction"], row["mirror"]
        count = facts[recipe].machine_count
        plan = strip_local_power(oriented_fluid_machine_row(
            recipe, count, origin, direction=direction, mirror=mirror,
        ))
        plans.append(plan)
        row_segments = oriented_fluid_network_segments(
            recipe, count, origin, direction=direction, mirror=mirror,
        )
        segments.extend(row_segments)
        for segment in row_segments:
            fluid = segment["fluid"]
            machine_tiles.setdefault(fluid, set()).update(
                tile for action in actions(plan) if action.get("entity") == facts[recipe].machine
                for tile in entity_footprint_tiles(action)
            )
            header = oriented_header_attachment(
                recipe, fluid, count, origin, direction=direction, mirror=mirror,
            )
            ports.setdefault(fluid, []).append({"recipe": recipe, **header})

    shared = {fluid for fluid, entries in ports.items() if len(entries) > 1}
    if not isinstance(trunk_columns, Mapping) or set(trunk_columns) != shared:
        raise ValueError("trunk_columns must name exactly the shared fluids")
    if (isinstance(route_order, (str, bytes)) or not isinstance(route_order, Sequence)
            or len(route_order) != len(shared) or set(route_order) != shared):
        raise ValueError("route_order must name each shared fluid exactly once")
    trunks = {fluid: _integer(column, "trunk column") for fluid, column in trunk_columns.items()}
    coordinates.extend((column, coordinates[0][1]) for column in trunks.values())
    if (max(point[0] for point in coordinates) - min(point[0] for point in coordinates) > _MAX_SPAN
            or max(point[1] for point in coordinates) - min(point[1] for point in coordinates) > _MAX_SPAN):
        raise ValueError("row and trunk span exceeds the bounded search area")

    _check_fluid_overlap(segments)
    for fluid in route_order:
        attachments = [tuple(port["attach"]) for port in ports[fluid]]
        args = (attachments[0], attachments[1:], fluid, trunks[fluid])
        link = generate_fluid_chain_link(*args, foreign=segments)
        link_segments = fluid_chain_link_segments(*args, foreign=segments)
        plans.append(link)
        segments.extend(link_segments)
        _check_fluid_overlap(segments)

    spans = _check_spans(segments, machine_tiles)
    combined = {"phases": [phase for plan in plans for phase in plan["phases"]]}
    validate_build_plan(combined)
    validate_planned_underground_pairs(combined, segments)
    assert_no_production_infinity([("oil-block", combined)])
    if placement_capture is not None:
        artifact = derive_ghost_artifact_from_runtime_catalog(combined, placement_capture=placement_capture)
    else:
        artifact = derive_ghost_artifact(combined, item_mapping=item_mapping)
    artifact["pending_checks"].extend([
        "fluid_sources", "fluid_transport", "fluid_measurement",
    ])
    artifact["unknowns"].update({
        "fluid_sources": "External source rates are model facts, not available supply or connected ports.",
        "fluid_transport": "World pipes, underground pairing and pipeline capacity are not verified.",
        "fluid_measurement": "No scoped runtime fluid counter or sustained delivery was observed.",
    })
    catalog_bytes = json.dumps(catalog, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False, allow_nan=False).encode("utf-8")
    artifact["planning_context"] = {
        "version": "planner-context-v1", "trust": "unverified", "data": {
            "kind": "bounded-oil-block-v1",
            "canonical_catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
            "machines": [{"recipe": fact.recipe, "machine": fact.machine,
                          "count": fact.machine_count,
                          "required_crafts_per_second": str(fact.required_crafts_per_second),
                          "craft_seconds": str(fact.craft_seconds),
                          "machine_speed": str(fact.machine_speed),
                          "exact_machines": str(fact.exact_machines),
                          "nominal_crafts_per_second": str(fact.nominal_crafts_per_second),
                          "spare_crafts_per_second": str(fact.spare_crafts_per_second)}
                         for fact in sizing.facts],
            "fluid_rates": [{"fluid": source.fluid,
                             "consumption_per_second": str(source.consumption_rate),
                             "internal_production_per_second": str(source.internally_produced_rate),
                             "external_source_per_second": str(source.external_source_rate),
                             "final_demand_per_second": str(source.final_demand_rate),
                             "source_cap_per_second": None if source.cap is None else str(source.cap)}
                            for source in sizing.fluid_sources],
            "rows": {recipe: {"origin": list(rows[recipe]["origin"]),
                              "direction": rows[recipe]["direction"], "mirror": rows[recipe]["mirror"]}
                     for recipe in sorted(facts)},
            "route_order": list(route_order), "trunk_columns": trunks,
            "ports": ports, "pipeline_spans": spans,
            "fluid_topology_intent": fluid_topology_intent(combined, segments, permitted_fluid_attachments),
        },
    }
    return {"artifact": artifact, "machine_facts": sizing, "ports": ports, "pipeline_spans": spans,
            "segments": segments,
            "non_claims": (
                "offline candidate; no runtime, dispatch, or construction authority",
                "source supply, terrain, power, logistics, pipeline capacity, "
                "world underground pairing and measured throughput are not verified; "
                "pipeline spans apply the repository model only to this candidate",
                "item mapping and ports are caller assertions, not verified inventories or connections",
            )}


def build_and_bind_oil_block(
    catalog: object, *, balance: object, machine_speeds: object,
    rows: Mapping[str, Mapping[str, object]], trunk_columns: Mapping[str, int],
    route_order: Sequence[str], item_mapping: Mapping[str, object] | None = None,
    placement_capture: Mapping[str, object] | None = None, max_machines: int,
    scope: ConstructionScope, project_id: str, transaction_id: str, throughput_output: str,
    expected_artifact_sha256: str | None = None, expected_plan_hash: str | None = None,
    permitted_fluid_attachments: Sequence[Mapping[str, object]] = (),
) -> dict:
    """Bind a rebuilt candidate to its exact positive net fluid output demand.

    The chosen output must be a generated fluid with no external source in the
    balance; otherwise an external input could be mistaken for block output.
    The resulting scope and all receipt observations remain caller assertions.
    This function neither verifies a site nor grants dispatch authority.
    """
    if not isinstance(throughput_output, str) or not throughput_output:
        raise ValueError("throughput_output must be a non-empty fluid name")
    if placement_capture is not None:
        if (not isinstance(placement_capture, Mapping) or not isinstance(scope, ConstructionScope)
                or placement_capture.get("surface") != scope.surface
                or placement_capture.get("force") != scope.force):
            raise ValueError("placement capture scope must match construction scope")
    candidate = build_oil_block_artifact(
        catalog, balance=balance, machine_speeds=machine_speeds, rows=rows,
        trunk_columns=trunk_columns, route_order=route_order, item_mapping=item_mapping,
        placement_capture=placement_capture,
        max_machines=max_machines, permitted_fluid_attachments=permitted_fluid_attachments,
    )
    output = next((source for source in candidate["machine_facts"].fluid_sources
                   if source.fluid == throughput_output), None)
    if (output is None or output.final_demand_rate <= 0 or output.external_source_rate != 0
            or not any(port["role"] == "output" for port in candidate["ports"].get(throughput_output, ()))):
        raise ValueError("commissioning output must be a positive internally produced fluid demand without external source")
    bound = bind_artifact_to_construction_plan(
        candidate["artifact"], scope=scope, project_id=project_id, transaction_id=transaction_id,
        throughput_output=throughput_output, throughput_target=output.final_demand_rate,
        throughput_unit=FLUID_PER_SECOND, expected_artifact_sha256=expected_artifact_sha256,
        expected_plan_hash=expected_plan_hash,
    )
    return {"candidate": candidate, "bound": bound}
