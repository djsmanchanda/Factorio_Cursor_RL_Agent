# Path: orchestrator/stage_recovery.py
# Purpose: Bounded recovery of deterministic existing stages whose declared ingredient transport is missing or broken.

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence

from orchestrator import live_base
from orchestrator.game_bridge import GameBridge
from orchestrator.stage_services import StuckError, _diagnose_machines
from orchestrator.stage_transport import (
    _swap_infinity_chests,
    _transport_mode,
    ensure_ingredient_transport,
)
from planners.local_layout_planner import LocalLayoutPlanner
from planners.recipe_data import LINE_RECIPES
from tools.rcon_client import RconClient

Point = tuple[float, float]


def _existing_stage_feed_plan(
    recipe: str, machine_positions: Sequence[Point],
    *, selected_modes: Mapping[str, str] | None = None,
) -> tuple[dict, dict[str, str]]:
    """Reconstruct the supported chest-row layout using the shared constructor."""
    if not machine_positions:
        raise StuckError(f"existing {recipe} stage has no machine positions")
    ordered = sorted(machine_positions)
    y = ordered[0][1]
    expected = [(ordered[0][0] + 3 * index, y) for index in range(len(ordered))]
    if list(ordered) != expected:
        raise StuckError(
            f"existing {recipe} machines do not match the deterministic line geometry; "
            "ingredient transport cannot be repaired safely"
        )
    origin_x = ordered[0][0] - 1.5
    origin_y = y - 3.5
    if not origin_x.is_integer() or not origin_y.is_integer():
        raise StuckError(
            f"existing {recipe} stage origin is not tile-aligned; "
            "ingredient transport cannot be reconstructed safely"
        )
    modes = {
        ingredient: _transport_mode(recipe, ingredient, len(ordered))
        for ingredient in LINE_RECIPES[recipe]["ingredients"]
    }
    if selected_modes is not None:
        if set(selected_modes) != set(modes) or any(
                mode not in {'belt', 'logistic'} for mode in selected_modes.values()):
            raise ValueError('incomplete selected ingredient transport modes')
        modes = dict(selected_modes)
    plan = LocalLayoutPlanner().generate_line_layout(
        recipe, len(ordered), int(origin_x), int(origin_y),
        feed_style="chest", terminal_collector=True,
    )
    return plan, modes


def _existing_stage_feed_contract(
    recipe: str, machine_positions: Sequence[Point],
    *, selected_modes: Mapping[str, str] | None = None,
) -> tuple[dict[str, Point], dict[str, str], dict[str, str]]:
    """Reconstruct feed positions and prototypes using the line constructor."""
    plan, modes = _existing_stage_feed_plan(recipe, machine_positions, selected_modes=selected_modes)
    feeds = _swap_infinity_chests(plan, modes)
    actions = {
        (action["position"]["x"], action["position"]["y"]): action["entity"]
        for phase in plan["phases"] for action in phase["actions"]
        if "position" in action and "entity" in action
    }
    return feeds, modes, {ingredient: actions[position] for ingredient, position in feeds.items()}


def _owned_stage_feed_contract(bridge, surface, force, recipe, machine_positions):
    """Bind repair to an acknowledged selected row, never to the chest found live.

    Existing non-journalled deterministic callers retain constructor defaults.
    A scoped session needs selected evidence, including a returned submission
    whose enclosing operation stopped during construction.
    """
    invocations = getattr(bridge, 'operation_invocations', None)
    if invocations is None:
        return _existing_stage_feed_contract(recipe, machine_positions)

    def unresolved(reason):
        raise StuckError(f'existing {recipe} feed contract is unresolved: {reason}',
                         code='stage_feed_contract_unknown', classification='intended_difficulty',
                         state='planned', details={'recipe': recipe, 'surface': surface, 'force': force,
                                                   'reason': reason})

    if (surface, force) != ('nauvis', 'player'):
        unresolved('scope mismatch')
    # Derive allowed endpoint identities from the same constructor used to build
    # the row. The submitted plan chooses among them; live entities never do.
    ingredients = LINE_RECIPES[recipe]['ingredients']
    templates = {mode: _existing_stage_feed_contract(recipe, machine_positions,
                 selected_modes={i: mode for i in ingredients}) for mode in ('belt', 'logistic')}
    feeds = templates['belt'][0]
    positions = set(map(tuple, machine_positions))

    def selected_topology(modes):
        plan, _ = _existing_stage_feed_plan(recipe, machine_positions, selected_modes=modes)
        feed_ingredients = {tuple(a['position'][k] for k in ('x', 'y')): a['infinity_filter']
                            for phase in plan['phases'] for a in phase['actions']
                            if a.get('entity') == 'infinity-chest'}
        _swap_infinity_chests(plan, modes)
        topology = {tuple(a['position'][k] for k in ('x', 'y')): (a['entity'], a.get('direction'))
                    for phase in plan['phases'] for a in phase['actions']
                    if a.get('entity', '').endswith(('inserter', 'transport-belt'))
                    or tuple(a.get('position', {}).get(k) for k in ('x', 'y')) in feed_ingredients}
        return topology, feed_ingredients

    def compatible(action, point, topology, feed_ingredients, modes):
        expected_entity, expected_direction = topology[point]
        if action.get('entity') != expected_entity:
            return False
        # Configuration may omit unchanged directions; placements must prove them.
        if ('direction' in action or action.get('action_type') != 'configure_entity') and action.get('direction') != expected_direction:
            return False
        if point in feed_ingredients:
            requests = []
            if 'logistic_request' in action:
                requests.append(action['logistic_request'])
            requests.extend(action.get('logistic_requests', []))
            for section in action.get('logistic_sections', []):
                requests.extend(section['requests'])
            if action.get('clear_logistic_groups'):
                return False  # No proof that the selected demand survives clearing.
            if requests and (modes[feed_ingredients[point]] != 'logistic' or any(
                    not isinstance(request, dict) or request.get('name') != feed_ingredients[point]
                    or type(request.get('count')) is not int or request['count'] <= 0
                    for request in requests)):
                return False
        return True

    entries = list(invocations.returned_plans())  # Preserves dispatch/legacy fences.
    candidates = []
    for entry in entries:
        report = entry.get('report') or {}
        acknowledged = (report.get('ok') is True and type(report.get('tick')) is int
                        and report.get('failed_placements') == 0 and report.get('recipe_failures') == 0)
        actions = [a for phase in entry['plan']['phases'] for a in phase['actions']]
        placed = [a for a in actions if a.get('action_type') in {'place_entity', 'place_ghost'}]
        row = {tuple(a['position'][k] for k in ('x', 'y')) for a in placed
               if a.get('recipe') == recipe and a.get('entity') in live_base.ASSEMBLER_TIERS}
        if not acknowledged or row != positions:
            continue
        modes = {}
        for ingredient, point in feeds.items():
            names = {a.get('entity') for a in placed
                     if a.get('position') == dict(zip(('x', 'y'), point))}
            matches = [mode for mode, (_, _, prototypes) in templates.items()
                       if names == {prototypes[ingredient]}]
            if len(matches) != 1:
                break
            modes[ingredient] = matches[0]
        if len(modes) == len(ingredients):
            topology, feed_ingredients = selected_topology(modes)
            # A coincident chest is insufficient: prove every feeder, lane and
            # inserter direction in the supported constructor topology.
            if all((matches := [a for a in placed if a.get('position') == dict(zip(('x', 'y'), point))])
                   and all(compatible(a, point, topology, feed_ingredients, modes) for a in matches)
                   for point in topology):
                candidates.append((report['tick'], modes))
    if not candidates:
        unresolved('no acknowledged complete selected row')
    modes = candidates[0][1]
    if any(candidate != modes for _, candidate in candidates):
        unresolved('conflicting selected transport modes')
    contract = _existing_stage_feed_contract(recipe, machine_positions, selected_modes=modes)
    topology, feed_ingredients = selected_topology(modes)
    targets = positions | topology.keys()
    first_tick = min(tick for tick, _ in candidates)
    for entry in entries:
        actions = [a for phase in entry['plan']['phases'] for a in phase['actions']]
        touched = [a for a in actions if tuple(a.get('position', {}).get(k) for k in ('x', 'y')) in targets]
        if not touched:
            continue
        report = entry.get('report') or {}
        if (report.get('ok') is not True or type(report.get('tick')) is not int
                or report.get('failed_placements') != 0 or report.get('recipe_failures') != 0):
            unresolved('unverified submission touches the selected row')
        if report['tick'] < first_tick:
            continue
        for action in touched:
            point = tuple(action['position'][k] for k in ('x', 'y'))
            if (action.get('action_type') not in {'place_entity', 'place_ghost', 'configure_entity'}
                    or 'release_cell' in action
                    or (point in positions and 'recipe' in action and action['recipe'] != recipe)
                    or (point in topology and not compatible(action, point, topology, feed_ingredients, modes))):
                unresolved('selected row was superseded or changed')
    return contract


def _existing_stage_feed_positions(
    recipe: str, machine_positions: Sequence[Point],
) -> tuple[dict[str, Point], dict[str, str]]:
    feeds, modes, _ = _existing_stage_feed_contract(recipe, machine_positions)
    return feeds, modes


def repair_existing_ingredient_transport(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, machine_positions: Sequence[Point],
    ensure_source: Callable[[str], Point | None], emit: Callable[[str], None],
) -> bool:
    """Repair one existing starved stage without duplicating its machines.

    Returns False when an upstream stage was built/repaired this iteration and
    the caller should re-survey. A completed repair is verified by machine
    status after a bounded first-item transit window.
    """
    feeds, modes, prototypes = _owned_stage_feed_contract(
        bridge, surface, force, recipe, machine_positions)
    def validate_endpoints():
        for ingredient, position in sorted(feeds.items()):
            entity = live_base.entity_at(client, surface, position)
            expected = prototypes[ingredient]
            if entity is None or entity["name"] != expected:
                found = "nothing" if entity is None else entity["name"]
                raise StuckError(
                    f"existing {recipe} feed for {ingredient} expected {expected} at "
                    f"{position}, found {found}; refusing to guess a different endpoint"
                )

    validate_endpoints()

    sources: dict[str, Point] = {}
    for ingredient in sorted(feeds):
        source = ensure_source(ingredient)
        if source is None:
            return False
        if math.dist(source, feeds[ingredient]) == 0:
            raise StuckError(
                f"existing {recipe} feed for {ingredient} resolves to its own source chest"
            )
        sources[ingredient] = source

    # Upstream ensure may itself dispatch work. It cannot silently change the
    # destination contract between the first observation and transport repair.
    if _owned_stage_feed_contract(bridge, surface, force, recipe, machine_positions) != (feeds, modes, prototypes):
        raise StuckError(f'existing {recipe} feed contract changed during upstream work',
                         code='stage_feed_contract_changed')
    validate_endpoints()
    grace = 0.0
    for ingredient in sorted(feeds):
        grace = max(
            grace,
            ensure_ingredient_transport(
                client, bridge, surface, force, recipe, ingredient,
                sources[ingredient], feeds[ingredient], len(machine_positions), emit,
                reuse_existing=True,
                mode=modes[ingredient],
            ),
        )
    stuck = _diagnose_machines(
        client, surface, list(machine_positions), emit, grace_seconds=grace,
        bridge=bridge, force=force,
    )
    if stuck:
        empty = [
            ingredient for ingredient, position in sorted(feeds.items())
            if live_base.chest_contents(client, surface, position).get(ingredient, 0) == 0
        ]
        detail = f"; empty feeds: {empty}" if empty else ""
        raise StuckError(
            f"existing {recipe} transport repair did not restore production: {stuck}{detail}"
        )
    emit(f"existing {recipe} ingredient transport recovered")
    return True
