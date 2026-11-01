# Path: core/science_line_candidate.py
# Purpose: Integrate a provenanced catalog-demand result with explicit line
# geometry and fixed-recipe machine facts, offline and without deployment claims.

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
import hashlib
import json

from core.catalog_demand import CatalogDemandResult
from core.fixed_recipe_balance import FixedRecipeBalanceResult, RecipeSignature
from core.fixed_recipe_machines import (
    FixedRecipeMachineError,
    FixedRecipeMachineFact,
    compute_fixed_recipe_machines,
)
from planners.recipe_data import LINE_RECIPES, MACHINE_SPEEDS
from planners.science_block_geometry import build_science_block_geometry

# Deliberately unresolved: these facts require the target world, its power and
# logistics networks, and measured runtime throughput.
_ADDITIONAL_UNKNOWNS = {
    "transport": (
        "Belt, inserter and pipe throughput is not measured; the line geometry "
        "only places its declared tiers."
    ),
    "bot_funding": (
        "No material contribution, construction-bot availability or roboport "
        "coverage is established; ghost placement is not funding."
    ),
    "runtime_throughput": (
        "No runtime throughput is observed; machine facts come from catalog "
        "energy_ticks and crafting_speed, not from a running factory."
    ),
}

NON_CLAIMS = (
    "offline integration only; no site, terrain, existing entity or logistics observation",
    "no transport, power, bot funding, construction or runtime throughput claim",
    "machine counts are nominal algebraic facts from catalog energy_ticks and crafting_speed",
    "geometry is not a player-force build claim and must not be labelled deployable",
    "direct placements and removals are reported grossly and are never converted to ghosts",
)


class ScienceLineCandidateReason(str, Enum):
    """Typed fail-closed reasons for the offline science-line integration."""

    INVALID_DEMAND_RESULT = "invalid_demand_result"
    INVALID_CATALOG = "invalid_catalog"
    CATALOG_MISMATCH = "catalog_mismatch"
    INVALID_LINE_SPEC = "invalid_line_spec"
    INCOMPATIBLE_RECIPE = "incompatible_recipe"
    RECIPE_NOT_IN_BALANCE = "recipe_not_in_balance"
    UNSUPPORTED_BALANCE = "unsupported_balance"
    UNSUPPORTED_GRAPH = "unsupported_graph"
    UNSUPPORTED_GEOMETRY = "unsupported_geometry"
    INVALID_MACHINE_SELECTION = "invalid_machine_selection"
    MACHINE_GEOMETRY_MISMATCH = "machine_geometry_mismatch"
    INVALID_REQUESTED_COUNT = "invalid_requested_count"
    EXPLICIT_COUNT_DISAGREEMENT = "explicit_count_disagreement"


class ScienceLineCandidateError(ValueError):
    """The candidate cannot be formed without inventing an unsupported fact."""

    def __init__(self, reason: ScienceLineCandidateReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.code = reason.value


@dataclass(frozen=True)
class ScienceLineCandidate:
    """Honest comparison of an explicit line count with the required count."""

    recipe: str
    machine: str
    requested_machine_count: int
    required_machine_count: int
    exact_machines: Fraction
    count_status: str
    machine_shortfall: int
    machine_spare: int
    machine_fact: FixedRecipeMachineFact
    geometry: Mapping[str, object]
    counts: Mapping[str, int]
    ghost_entities: Mapping[str, int]
    direct_entities: Mapping[str, int]
    removed_entities: Mapping[str, int]
    has_direct_actions: bool
    non_executable_marker: str | None
    unknowns: Mapping[str, str]
    notes: tuple[str, ...] = NON_CLAIMS

    @property
    def deployable(self) -> bool:
        """This offline candidate never establishes a player-force build path."""

        return False

    @property
    def player_force_build_claim(self) -> bool:
        return False

    def as_dict(self) -> dict:
        """Return a plain mapping; counts and unknowns are copied unchanged."""

        return {
            "recipe": self.recipe,
            "machine": self.machine,
            "requested_machine_count": self.requested_machine_count,
            "required_machine_count": self.required_machine_count,
            "exact_machines": self.exact_machines,
            "count_status": self.count_status,
            "machine_shortfall": self.machine_shortfall,
            "machine_spare": self.machine_spare,
            "machine_fact": self.machine_fact,
            "geometry": self.geometry,
            "counts": dict(self.counts),
            "ghost_entities": dict(self.ghost_entities),
            "direct_entities": dict(self.direct_entities),
            "removed_entities": dict(self.removed_entities),
            "has_direct_actions": self.has_direct_actions,
            "non_executable_marker": self.non_executable_marker,
            "unknowns": dict(self.unknowns),
            "deployable": self.deployable,
            "player_force_build_claim": self.player_force_build_claim,
            "notes": self.notes,
        }


def build_science_line_candidate(
    demand_result: CatalogDemandResult,
    catalog: Mapping[str, object],
    *,
    recipe: str,
    line_spec: Mapping[str, object],
    machine_by_recipe: Mapping[str, str] | None,
    catalog_bytes: bytes,
    requested_machine_count: int | None = None,
) -> ScienceLineCandidate:
    """Compare one explicit line with a provenanced demand result, offline.

    ``demand_result`` is the exact plan returned by ``plan_catalog_demand``.
    ``catalog`` must be the catalog used to produce it and ``catalog_bytes``
    must be the exact bytes that produced it: every call hash-checks those
    bytes against the result provenance, parses them back and requires the
    passed mapping to equal the parsed catalog. Missing bytes are rejected
    because force, tick and recipe parts alone cannot prove machine facts such
    as crafting speed. ``recipe`` is the line's chosen product and
    ``line_spec`` is the explicit specification accepted by the existing
    geometry adapter.

    Every selected recipe in the balance must be known to the existing line
    knowledge ``planners.recipe_data.LINE_RECIPES``; otherwise the graph is
    rejected instead of inventing a machine or layout. ``machine_by_recipe``
    must explicitly and completely map every selected balance recipe to a
    machine; no tier is inferred from line knowledge. The chosen recipe's
    selected machine must equal the machine actually generated for that
    recipe.

    The return value reports the exact required count and whether the explicitly
    requested count is algebraically adequate or undersized. It never changes the
    requested count, the demand rates, the recipe, the machine tier or the plan,
    and it never claims site, transport, power, bot funding, runtime throughput
    or deployability.
    """

    if not isinstance(demand_result, CatalogDemandResult):
        raise ScienceLineCandidateError(
            ScienceLineCandidateReason.INVALID_DEMAND_RESULT,
            "demand_result must be a CatalogDemandResult from plan_catalog_demand",
        )
    if not isinstance(catalog, Mapping):
        raise ScienceLineCandidateError(
            ScienceLineCandidateReason.INVALID_CATALOG,
            "catalog must be the exact mapping used to produce demand_result",
        )
    if not isinstance(recipe, str) or not recipe:
        raise ScienceLineCandidateError(
            ScienceLineCandidateReason.INCOMPATIBLE_RECIPE,
            "recipe must be a nonempty line recipe name",
        )
    if not isinstance(line_spec, Mapping):
        raise ScienceLineCandidateError(
            ScienceLineCandidateReason.INVALID_LINE_SPEC,
            "line_spec must be the explicit mapping used by the geometry adapter",
        )

    _verify_catalog(demand_result, catalog, catalog_bytes)

    spec_recipe = line_spec.get("recipe")
    if spec_recipe != recipe:
        raise ScienceLineCandidateError(
            ScienceLineCandidateReason.INCOMPATIBLE_RECIPE,
            f"line spec recipe {spec_recipe!r} does not match chosen recipe {recipe!r}",
        )

    spec_count = line_spec.get("machine_count")
    if requested_machine_count is None:
        requested = spec_count
    else:
        requested = _positive_count(
            requested_machine_count, "requested_machine_count"
        )
        if requested != spec_count:
            raise ScienceLineCandidateError(
                ScienceLineCandidateReason.EXPLICIT_COUNT_DISAGREEMENT,
                f"requested_machine_count {requested} disagrees with explicit line "
                f"spec machine_count {spec_count!r}",
            )
    requested = _positive_count(requested, "line spec machine_count")

    balance = demand_result.balance
    if not isinstance(balance, FixedRecipeBalanceResult):
        raise ScienceLineCandidateError(
            ScienceLineCandidateReason.UNSUPPORTED_BALANCE,
            "demand_result does not carry a FixedRecipeBalanceResult",
        )
    if balance.status != "unique" or balance.feasible is not True or balance.shortages:
        raise ScienceLineCandidateError(
            ScienceLineCandidateReason.UNSUPPORTED_BALANCE,
            "only a unique, feasible, shortage-free balance can size line machines",
        )

    names = tuple(balance.recipe_names)
    if recipe not in names:
        raise ScienceLineCandidateError(
            ScienceLineCandidateReason.RECIPE_NOT_IN_BALANCE,
            f"chosen recipe {recipe!r} is not selected by the demand balance",
        )

    try:
        geometry = build_science_block_geometry(line_spec)
    except ValueError as exc:
        raise ScienceLineCandidateError(
            ScienceLineCandidateReason.UNSUPPORTED_GEOMETRY,
            f"explicit line spec is not supported by the geometry adapter: {exc}",
        ) from exc

    machine_map = _machine_map(names, machine_by_recipe)
    try:
        machines = compute_fixed_recipe_machines(
            catalog,
            balance=balance,
            machine_by_recipe=machine_map,
        )
    except FixedRecipeMachineError as exc:
        reason = _machine_error_reason(str(exc))
        raise ScienceLineCandidateError(reason, str(exc)) from exc

    fact = next((item for item in machines.facts if item.recipe == recipe), None)
    if fact is None:
        raise ScienceLineCandidateError(
            ScienceLineCandidateReason.UNSUPPORTED_BALANCE,
            f"fixed-recipe machine facts do not include chosen recipe {recipe!r}",
        )

    geometry_machine = geometry["spec"].get("machine") or LINE_RECIPES[recipe]["machine"]
    if fact.machine != geometry_machine:
        raise ScienceLineCandidateError(
            ScienceLineCandidateReason.MACHINE_GEOMETRY_MISMATCH,
            f"selected machine {fact.machine!r} for {recipe!r} differs from the "
            f"geometry's actual machine {geometry_machine!r}",
        )

    recipe_spec = LINE_RECIPES[recipe]
    expected_signature = RecipeSignature.from_parts(
        recipe,
        [(name, "item", Fraction(str(amount)))
         for name, amount in zip(recipe_spec["ingredients"], recipe_spec["amounts"], strict=True)]
        + [(name, "fluid", Fraction(str(amount)))
           for name, amount in recipe_spec.get("fluid_ingredients", {}).items()],
        [(recipe, "item", Fraction(str(recipe_spec["product_amount"])))],
    )
    actual_signature = next((entry for entry in balance.recipe_signatures if entry.recipe == recipe), None)
    if (actual_signature != expected_signature
            or fact.base_craft_seconds != Fraction(str(recipe_spec["craft_time"]))
            or fact.crafting_speed != Fraction(str(MACHINE_SPEEDS[geometry_machine]))):
        raise ScienceLineCandidateError(
            ScienceLineCandidateReason.INCOMPATIBLE_RECIPE,
            "catalog recipe or machine speed differs from the line geometry assumptions",
        )

    _verify_geometry_machines(
        geometry,
        recipe=recipe,
        machine=fact.machine,
        expected_count=requested,
    )

    required = fact.machine_count
    if requested < required:
        status = "undersized"
        shortfall = required - requested
        spare = 0
    else:
        status = "adequate"
        shortfall = 0
        spare = requested - required

    unknowns = dict(geometry.get("unknowns", {}))
    for name, description in _ADDITIONAL_UNKNOWNS.items():
        unknowns.setdefault(name, description)

    return ScienceLineCandidate(
        recipe=recipe,
        machine=fact.machine,
        requested_machine_count=requested,
        required_machine_count=required,
        exact_machines=fact.exact_machines,
        count_status=status,
        machine_shortfall=shortfall,
        machine_spare=spare,
        machine_fact=fact,
        geometry=geometry,
        counts=dict(geometry.get("counts", {})),
        ghost_entities=dict(geometry.get("ghost_entities", {})),
        direct_entities=dict(geometry.get("direct_entities", {})),
        removed_entities=dict(geometry.get("removed_entities", {})),
        has_direct_actions=bool(geometry.get("has_direct_actions")),
        non_executable_marker=geometry.get("non_executable_marker"),
        unknowns=unknowns,
        notes=NON_CLAIMS,
    )


def _positive_count(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ScienceLineCandidateError(
            ScienceLineCandidateReason.INVALID_REQUESTED_COUNT,
            f"{label} must be a positive integer, not {value!r}",
        )
    return value


def _verify_catalog(
    demand_result: CatalogDemandResult,
    catalog: Mapping[str, object],
    catalog_bytes: bytes | None,
) -> None:
    provenance = demand_result.provenance
    if not isinstance(catalog_bytes, (bytes, bytearray)):
        raise ScienceLineCandidateError(
            ScienceLineCandidateReason.INVALID_CATALOG,
            "catalog_bytes is required and must be the exact bytes supplied to "
            "plan_catalog_demand; force, tick and recipe parts alone cannot prove "
            "machine facts such as crafting speed",
        )
    if catalog.get("force") != provenance.force:
        raise ScienceLineCandidateError(
            ScienceLineCandidateReason.CATALOG_MISMATCH,
            f"catalog force {catalog.get('force')!r} does not match provenanced "
            f"force {provenance.force!r}",
        )
    if catalog.get("tick") != provenance.export_tick:
        raise ScienceLineCandidateError(
            ScienceLineCandidateReason.CATALOG_MISMATCH,
            f"catalog tick {catalog.get('tick')!r} does not match provenanced "
            f"export tick {provenance.export_tick!r}",
        )
    payload = bytes(catalog_bytes)
    actual = hashlib.sha256(payload).hexdigest()
    if actual != provenance.catalog_sha256:
        raise ScienceLineCandidateError(
            ScienceLineCandidateReason.CATALOG_MISMATCH,
            f"catalog bytes hash to {actual}, not the provenanced "
            f"{provenance.catalog_sha256}",
        )
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScienceLineCandidateError(
            ScienceLineCandidateReason.INVALID_CATALOG,
            f"catalog bytes are not the valid JSON that produced the result: {exc}",
        ) from exc
    if parsed != catalog:
        raise ScienceLineCandidateError(
            ScienceLineCandidateReason.CATALOG_MISMATCH,
            "catalog mapping differs from the provenanced catalog bytes",
        )


def _machine_map(
    names: Sequence[str],
    machine_by_recipe: Mapping[str, str] | None,
) -> dict[str, str]:
    selected = set(names)
    unsupported = sorted(name for name in selected if name not in LINE_RECIPES)
    if unsupported:
        raise ScienceLineCandidateError(
            ScienceLineCandidateReason.UNSUPPORTED_GRAPH,
            "demand graph selects recipe(s) with no supported line geometry: "
            + ", ".join(map(repr, unsupported)),
        )

    overrides: dict[str, str] = {}
    if not isinstance(machine_by_recipe, Mapping):
        raise ScienceLineCandidateError(
            ScienceLineCandidateReason.INVALID_MACHINE_SELECTION,
            "machine_by_recipe must explicitly map every selected recipe to a "
            "machine; no machine tier is inferred from line knowledge",
        )
    for name, machine in machine_by_recipe.items():
        if not isinstance(name, str) or not name:
            raise ScienceLineCandidateError(
                ScienceLineCandidateReason.INVALID_MACHINE_SELECTION,
                "machine_by_recipe keys must be nonempty recipe names",
            )
        if name not in selected:
            raise ScienceLineCandidateError(
                ScienceLineCandidateReason.INVALID_MACHINE_SELECTION,
                f"machine_by_recipe maps unselected recipe {name!r}",
            )
        if not isinstance(machine, str) or not machine:
            raise ScienceLineCandidateError(
                ScienceLineCandidateReason.INVALID_MACHINE_SELECTION,
                f"machine for recipe {name!r} must be a nonempty string",
            )
        overrides[name] = machine

    missing = sorted(selected - set(overrides))
    if missing:
        raise ScienceLineCandidateError(
            ScienceLineCandidateReason.INVALID_MACHINE_SELECTION,
            "machine_by_recipe is incomplete; missing explicit machine(s) for "
            + ", ".join(map(repr, missing)),
        )

    return {name: overrides[name] for name in names}


def _machine_error_reason(message: str) -> ScienceLineCandidateReason:
    lowered = message.lower()
    if "provenance" in lowered or "not in the catalog" in lowered:
        return ScienceLineCandidateReason.CATALOG_MISMATCH
    if "underdetermined" in lowered or "inconsistent" in lowered:
        return ScienceLineCandidateReason.UNSUPPORTED_BALANCE
    return ScienceLineCandidateReason.UNSUPPORTED_GEOMETRY


def _verify_geometry_machines(
    geometry: Mapping[str, object],
    *,
    recipe: str,
    machine: str,
    expected_count: int,
) -> None:
    plan = geometry.get("plan")
    if not isinstance(plan, Mapping):
        raise ScienceLineCandidateError(
            ScienceLineCandidateReason.UNSUPPORTED_GEOMETRY,
            "geometry did not return a build plan",
        )

    ghost_machines = 0
    direct_machines = 0
    for phase in plan.get("phases", []):
        for action in phase.get("actions", []):
            action_type = action.get("action_type")
            entity = action.get("entity")
            if action_type in ("place_ghost", "place_tile_ghost"):
                if entity not in MACHINE_SPEEDS:
                    continue
                placed_recipe = action.get("recipe")
                if entity != machine:
                    raise ScienceLineCandidateError(
                        ScienceLineCandidateReason.MACHINE_GEOMETRY_MISMATCH,
                        f"geometry places production machine {entity!r} for recipe "
                        f"{placed_recipe!r}, not expected machine {machine!r} for "
                        f"chosen recipe {recipe!r}",
                    )
                if placed_recipe is not None and placed_recipe != recipe:
                    raise ScienceLineCandidateError(
                        ScienceLineCandidateReason.MACHINE_GEOMETRY_MISMATCH,
                        f"geometry places machine {machine!r} for recipe "
                        f"{placed_recipe!r}, not chosen recipe {recipe!r}",
                    )
                ghost_machines += 1
            elif action_type == "place_entity":
                if entity in MACHINE_SPEEDS:
                    direct_machines += 1

    if direct_machines:
        raise ScienceLineCandidateError(
            ScienceLineCandidateReason.UNSUPPORTED_GEOMETRY,
            f"geometry directly places {direct_machines} production machine "
            "entity/entities; direct machine placements are not a ghost build path",
        )
    if ghost_machines != expected_count:
        raise ScienceLineCandidateError(
            ScienceLineCandidateReason.MACHINE_GEOMETRY_MISMATCH,
            f"geometry places {ghost_machines} ghost {machine!r} machine(s), not the "
            f"explicitly requested {expected_count}",
        )
