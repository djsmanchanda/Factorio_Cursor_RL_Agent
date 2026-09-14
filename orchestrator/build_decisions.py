# Path: orchestrator/build_decisions.py
# Purpose: The judgement calls a build pass makes -- what is limiting a product, where a line should sit, and which inserter tier it can both use and afford -- kept apart from the code that acts on them.

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Callable

from core.science_recipe_graph import NAUVIS_DIRECT_RESOURCE_INPUTS
from orchestrator import live_base
from planners.local_layout_planner import LocalLayoutPlanner
from planners.recipe_data import (
    FEED_HEADROOM,
    LINE_RECIPES,
    inserter_tiers_covering,
    machine_handled_rates,
    machine_ingredient_rates,
)
from tools.rcon_client import RconClient

Point = tuple[float, float]


@dataclass(frozen=True)
class ReplenishmentDiagnosis:
    """The next capability to restore before asking raw extraction to grow.

    ``expansion_target`` deliberately ignores whether an intermediate has a
    producer.  That is useful for sizing an already-running chain, but it is
    unsafe for recovery: a missing stick assembler makes more iron irrelevant.
    Keep the two decisions separate so callers can first establish the missing
    capability, then use the existing extraction walk for real throughput.
    """

    target: str | None
    kind: str  # ``missing_producer``, ``extraction``, or ``unsupported``
    path: tuple[str, ...]

def _mineable(recipe: str) -> bool:
    """Whether this recipe is a supported direct resource-extraction stage."""
    spec = LINE_RECIPES[recipe]
    if not spec.get("direct_extraction", False):
        return False
    ingredients = spec["ingredients"]
    return len(ingredients) == 1 and ingredients[0] in NAUVIS_DIRECT_RESOURCE_INPUTS


def may_consume_stocked_inputs(
    *, upgrade_bootstrap: bool, promote_to_line: bool,
) -> bool:
    """Whether this build may satisfy an ingredient from a stocked buffer
    instead of resolving a producing stage for it.

    A readiness MALL CELL may: it is requester-fed, so bots really do supply it
    from whatever is in a chest. Demanding a live upstream line first would
    deadlock bootstrap items such as inserters, whose plates and gears are
    already stocked while the iron-plate line itself needs those very inserters
    as construction ghosts.

    A PROMOTED LINE may not: it is belt-fed from a producing stage, so it needs
    a real source POSITION, which a stocked buffer cannot give. Taking the
    shortcut left build_conversion_stage with nothing to route from and ended a
    run on "iron-gear-wheel feeds on ['iron-plate'], which have no producing
    stage to supply them" -- while 13 iron plates sat in a chest.
    """
    return not upgrade_bootstrap and not promote_to_line


def expansion_target(item: str, stock: Mapping[str, int]) -> str | None:
    """The deepest extraction stage that limits `item`, or None if none does.

    Bottlenecks are recursive. A is short because B is short because C is
    short, all the way down to ore, so raising A means raising whatever is
    actually starved beneath it. Checking only DIRECT ingredients for a
    mineable one gives up far too early: fast-transport-belt needs
    transport-belt and iron-gear-wheel, neither of which is mineable, so it
    deferred forever while its real constraint -- iron ore -- sat two levels
    down.

    At each level it follows the input the base is SHORTEST of, measured
    against what one craft consumes, so the walk tracks the live constraint
    rather than an arbitrary branch. `seen` guards against recipe cycles.
    """
    seen: set[str] = set()
    current = item
    while current in LINE_RECIPES and current not in seen:
        seen.add(current)
        if _mineable(current):
            return current
        spec = LINE_RECIPES[current]
        candidates = [
            ingredient for ingredient in spec["ingredients"]
            if ingredient in LINE_RECIPES and ingredient not in seen
        ]
        if not candidates:
            return None
        current = min(
            candidates,
            key=lambda ingredient: stock.get(ingredient, 0) / max(
                1, spec["amounts"][spec["ingredients"].index(ingredient)]
            ),
        )
    return None


def diagnose_replenishment(
    item: str,
    stock: Mapping[str, int],
    *,
    producer_is_live: Callable[[str], bool],
) -> ReplenishmentDiagnosis:
    """Find a missing intermediate producer before expanding extraction.

    The walk follows the same scarce-input rule as :func:`expansion_target`,
    but checks each non-extraction stage against measured producer evidence.
    It never guesses that a chest holding one item proves a future producer.
    A cycle stays an explicit unsupported diagnosis rather than becoming an
    unbounded recursive recovery request.
    """
    seen: set[str] = set()
    path: list[str] = []
    current = item
    while current in LINE_RECIPES:
        if current in seen:
            return ReplenishmentDiagnosis(None, "unsupported", tuple(path + [current]))
        seen.add(current)
        path.append(current)
        if _mineable(current):
            return ReplenishmentDiagnosis(current, "extraction", tuple(path))
        # The root is normally the stalled mall producer itself.  Do not ask
        # callers to rebuild it; inspect the first missing dependency below it.
        if current != item and not producer_is_live(current):
            return ReplenishmentDiagnosis(current, "missing_producer", tuple(path))
        spec = LINE_RECIPES[current]
        candidates = [
            ingredient for ingredient in spec["ingredients"]
            if ingredient in LINE_RECIPES and ingredient not in seen
        ]
        if not candidates:
            return ReplenishmentDiagnosis(None, "unsupported", tuple(path))
        current = min(
            candidates,
            key=lambda ingredient: stock.get(ingredient, 0) / max(
                1, spec["amounts"][spec["ingredients"].index(ingredient)],
            ),
        )
    return ReplenishmentDiagnosis(None, "unsupported", tuple(path))


def _heaviest_source(
    item: str, sources: Mapping[str, Point], machine_count: int,
) -> Point | None:
    """Where the line should sit: beside whichever input it consumes fastest.

    A belt run is proportional to distance, and the heaviest input is the one
    whose belt would cost the most and be most likely to fail routing. Falls
    back to None when no source is known, leaving the caller's own reference.
    """
    if not sources:
        return None
    spec = LINE_RECIPES[item]
    rates = dict(zip(spec["ingredients"], machine_ingredient_rates(item, machine_count)))
    return sources[max(sources, key=lambda ingredient: rates.get(ingredient, 0.0))]


def _stage_inserter_type(
    client: RconClient, surface: str, force: str, recipe: str, machine_count: int,
    belt_type: str, flow_direction: str, emit: Callable[[str], None],
) -> str:
    """The cheapest inserter tier that both CARRIES this line and can be BUILT.

    Rate alone is not enough. Several tiers are usually adequate, and demanding
    the cheapest one deadlocks whenever the base cannot make it yet: a smelter
    row needs inserters, inserters need iron plate, and iron plate needs the
    smelter row. Observed live -- the runner asked for 14 plain inserters,
    held 9, and spun on that cycle indefinitely.

    So among the tiers that carry the load, prefer one the base is already
    holding enough of. Substituting UP is always safe (more throughput than
    required, only more expensive), and an oversized inserter that exists beats
    a right-sized one that cannot be produced -- stability over optimality.
    """
    peak_rate = max(machine_handled_rates(recipe))
    covering = inserter_tiers_covering(peak_rate * FEED_HEADROOM)
    preferred = covering[0]
    needed = _line_inserter_count(recipe, machine_count, belt_type, preferred, flow_direction)
    stock = live_base.available_items(client, surface, force)
    if stock.get(preferred, 0) >= needed:
        emit(f"  {recipe} moves {peak_rate:.2f} item/s per machine -- using {preferred}")
        return preferred
    for tier in covering[1:]:
        if stock.get(tier, 0) >= needed:
            emit(f"  {recipe} moves {peak_rate:.2f} item/s per machine -- {preferred} "
                 f"fits but only {stock.get(preferred, 0)}/{needed} are in stock; "
                 f"using {tier} instead ({stock[tier]} available)")
            return tier
    # Nothing adequate is in stock: keep the right-sized choice so the parts
    # mall is asked for the cheapest part rather than an oversized one.
    emit(f"  {recipe} moves {peak_rate:.2f} item/s per machine -- using {preferred} "
         f"(only {stock.get(preferred, 0)}/{needed} in stock; no adequate tier is stocked)")
    return preferred


def _line_inserter_count(
    recipe: str, machine_count: int, belt_type: str, inserter_type: str,
    flow_direction: str,
) -> int:
    """How many of `inserter_type` this line's layout actually places."""
    plan = LocalLayoutPlanner().generate_line_layout(
        recipe, machine_count, 0, 0, belt_type=belt_type,
        inserter_type=inserter_type, feed_style="chest",
        terminal_collector=True, flow_direction=flow_direction,
    )
    return sum(
        1
        for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity") == inserter_type
    )
