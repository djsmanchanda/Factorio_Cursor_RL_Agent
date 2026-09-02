# Path: orchestrator/mall_bootstrap.py
# Purpose: Borrow and restore an existing paired-mall assembler for finite bootstrap production.

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Mapping

from planners.mall_layout import (
    generate_mall_provider_limit_update,
    recipe_group_name,
    recipe_group_requests,
    request_multiplier,
)
from planners.recipe_data import LINE_RECIPES
from planners.stock_gating import stock_gate
from tools.rcon_client import RconClient

Point = tuple[float, float]
_LOAN_PREFIX = "mall-bootstrap:"
_LOAN_V1_PREFIX = f"{_LOAN_PREFIX}v1:"
_LOAN_V2_PREFIX = f"{_LOAN_PREFIX}v2:"
_LOAN_V3_PREFIX = f"{_LOAN_PREFIX}v3:"
_LOAN_V4_PREFIX = f"{_LOAN_PREFIX}v4:"
_ASSEMBLER_TIERS = {
    "assembling-machine-1", "assembling-machine-2", "assembling-machine-3",
}


@dataclass(frozen=True)
class MallBootstrapLoan:
    """A live, tagged recipe loan encoded on the cell's requester chest."""

    original_recipe: str
    target_item: str
    target_count: int
    side: str
    requester_position: Point
    current_recipe: str
    spare_target_count: int | None = None
    step_recipe: str | None = None
    step_target_count: int | None = None
    step_baseline_finished: int | None = None
    step_required_crafts: int | None = None
    step_minimum_crafts: int | None = None
    completed_step_targets: tuple[tuple[str, int], ...] = ()
    machine_name: str = "assembling-machine-1"

    @property
    def production_target(self) -> int:
        return max(self.target_count, self.spare_target_count or self.target_count)

    @property
    def group(self) -> str:
        if (
            self.step_recipe is None
            or self.step_baseline_finished is None
            or self.step_required_crafts is None
            or self.step_minimum_crafts is None
        ):
            return bootstrap_loan_group(
                self.original_recipe, self.target_item, self.target_count, self.side,
            )
        if self.step_target_count is None:
            return (
                f"{_LOAN_V3_PREFIX}{self.original_recipe}:{self.target_item}:"
                f"{self.target_count}:{self.production_target}:{self.side}:"
                f"{self.step_recipe}:{self.step_baseline_finished}:"
                f"{self.step_required_crafts}:{self.step_minimum_crafts}"
            )
        completed = ",".join(
            f"{recipe}={count}"
            for recipe, count in self.completed_step_targets
        ) or "-"
        return (
            f"{_LOAN_V4_PREFIX}{self.original_recipe}:{self.target_item}:"
            f"{self.target_count}:{self.production_target}:{self.side}:"
            f"{self.step_recipe}:{self.step_target_count}:"
            f"{self.step_baseline_finished}:{self.step_required_crafts}:"
            f"{self.step_minimum_crafts}:{completed}"
        )

    def starting_step(
        self, recipe: str, target_count: int, baseline_finished: int,
        required_crafts: int, minimum_crafts: int,
    ) -> "MallBootstrapLoan":
        return replace(
            self, current_recipe=recipe, step_recipe=recipe,
            step_target_count=max(1, int(target_count)),
            spare_target_count=self.production_target,
            step_baseline_finished=max(0, int(baseline_finished)),
            step_required_crafts=max(1, int(required_crafts)),
            step_minimum_crafts=max(0, int(minimum_crafts)),
        )

    def credit_completed_step(
        self, recipe: str, target_count: int,
    ) -> "MallBootstrapLoan":
        """Persist prerequisite production even if logistics consumes its output."""
        completed = dict(self.completed_step_targets)
        completed[recipe] = max(completed.get(recipe, 0), int(target_count))
        return replace(self, completed_step_targets=tuple(sorted(completed.items())))

    @property
    def machine_position(self) -> Point:
        offset = -3.0 if self.side == "left" else 3.0
        return (self.requester_position[0] + offset, self.requester_position[1])

    @property
    def provider_position(self) -> Point:
        offset = -1.0 if self.side == "left" else 1.0
        return (self.requester_position[0], self.requester_position[1] + offset)


@dataclass(frozen=True)
class MallBootstrapStep:
    """The deepest temporarily craftable shortage and its absolute stock gate."""

    recipe: str
    target_count: int
    crafts: int


@dataclass(frozen=True)
class MallBootstrapExternalShortage:
    """A missing input that the borrowed assembler cannot manufacture."""

    item: str
    count: int


def bootstrap_loan_group(
    original_recipe: str, target_item: str, target_count: int, side: str,
) -> str:
    if side not in {"left", "right"} or target_count < 1:
        raise ValueError("Bootstrap mall loan needs a valid side and positive target")
    return (
        f"{_LOAN_V1_PREFIX}{original_recipe}:{target_item}:{target_count}:{side}"
    )


def parse_bootstrap_loan_group(
    group: str,
) -> tuple[
    str, str, int, int | None, str, str | None, int | None, int | None,
    int | None, int | None, tuple[tuple[str, int], ...],
] | None:
    if group.startswith(_LOAN_V1_PREFIX):
        fields = group[len(_LOAN_V1_PREFIX):].split(":")
        if len(fields) != 4:
            return None
        original, target, raw_count, side = fields
        raw_spare = None
        step, raw_step_target = None, None
        raw_baseline, raw_required, raw_minimum = None, None, None
        raw_completed = None
    elif group.startswith(_LOAN_V2_PREFIX):
        fields = group[len(_LOAN_V2_PREFIX):].split(":")
        if len(fields) != 7:
            return None
        original, target, raw_count, side, step, raw_baseline, raw_required = fields
        raw_spare, raw_step_target, raw_minimum = None, None, None
        raw_completed = None
    elif group.startswith(_LOAN_V3_PREFIX):
        fields = group[len(_LOAN_V3_PREFIX):].split(":")
        if len(fields) != 9:
            return None
        (
            original, target, raw_count, raw_spare, side, step,
            raw_baseline, raw_required, raw_minimum,
        ) = fields
        raw_step_target, raw_completed = None, None
    elif group.startswith(_LOAN_V4_PREFIX):
        fields = group[len(_LOAN_V4_PREFIX):].split(":")
        if len(fields) != 11:
            return None
        (
            original, target, raw_count, raw_spare, side, step,
            raw_step_target, raw_baseline, raw_required, raw_minimum,
            raw_completed,
        ) = fields
    else:
        return None
    try:
        count = int(raw_count)
        spare = int(raw_spare) if raw_spare is not None else None
        baseline = int(raw_baseline) if raw_baseline is not None else None
        required = int(raw_required) if raw_required is not None else None
        minimum = int(raw_minimum) if raw_minimum is not None else None
        step_target = int(raw_step_target) if raw_step_target is not None else None
    except ValueError:
        return None
    completed: tuple[tuple[str, int], ...] = ()
    if raw_completed not in {None, "-"}:
        parsed_completed: dict[str, int] = {}
        try:
            for entry in raw_completed.split(","):
                recipe, raw_target = entry.split("=", 1)
                parsed_completed[recipe] = int(raw_target)
        except (ValueError, TypeError):
            return None
        if any(not recipe or count < 1 for recipe, count in parsed_completed.items()):
            return None
        completed = tuple(sorted(parsed_completed.items()))
    if (
        not original or not target or count < 1
        or (spare is not None and spare < count)
        or side not in {"left", "right"}
        or (step is not None and (
            not step or baseline is None or baseline < 0
            or required is None or required < 1
            or (group.startswith(_LOAN_V4_PREFIX)
                and (step_target is None or step_target < 1))
            or (group.startswith(_LOAN_V3_PREFIX)
                and (minimum is None or minimum < 0 or minimum > required))
            or (group.startswith(_LOAN_V4_PREFIX)
                and (minimum is None or minimum < 0 or minimum > required))
        ))
    ):
        return None
    return (
        original, target, count, spare, side, step, step_target, baseline,
        required, minimum, completed,
    )


def _temporary_assembler_recipe(item: str) -> bool:
    spec = LINE_RECIPES.get(item)
    return bool(
        spec
        and spec.get("set_recipe", True)
        and spec.get("machine") in _ASSEMBLER_TIERS
        and not spec.get("fluid_ingredients")
    )


def next_bootstrap_step(
    target_item: str,
    target_count: int,
    usable_stock: Mapping[str, int],
    actual_stock: Mapping[str, int],
) -> MallBootstrapStep | None:
    """Choose the deepest missing solid recipe a borrowed assembler can make.

    ``target_count`` is the ledger's aggregate target for the root item. For
    prerequisites, stock reserved by other projects is unavailable, so a step
    produces an extra quantity above the physical count instead of stealing it.
    """
    if target_count < 1 or target_item not in LINE_RECIPES:
        return None
    root_missing = max(0, target_count - int(actual_stock.get(target_item, 0)))
    if root_missing == 0:
        return None

    def descend(item: str, missing: int, visiting: frozenset[str]) -> MallBootstrapStep | None:
        if missing <= 0 or item in visiting or not _temporary_assembler_recipe(item):
            return None
        spec = LINE_RECIPES[item]
        product_amount = max(1, math.floor(float(spec.get("product_amount", 1))))
        crafts = math.ceil(missing / product_amount)
        next_visiting = visiting | {item}
        for ingredient, amount in zip(
            spec["ingredients"], spec["amounts"], strict=True,
        ):
            required = math.ceil(float(amount) * crafts)
            available = int(usable_stock.get(ingredient, 0))
            if available >= required:
                continue
            nested = descend(ingredient, required - available, next_visiting)
            if nested is not None:
                return nested
        return MallBootstrapStep(
            recipe=item,
            target_count=int(actual_stock.get(item, 0)) + missing,
            crafts=crafts,
        )

    return descend(target_item, root_missing, frozenset())


def bootstrap_external_shortages(
    target_item: str,
    target_count: int,
    usable_stock: Mapping[str, int],
    actual_stock: Mapping[str, int],
) -> tuple[MallBootstrapExternalShortage, ...]:
    """Return missing recipe inputs that a rotating assembler cannot make.

    Temporarily craftable solid dependencies are traversed recursively. Their
    furnace, fluid, extraction, or otherwise non-assembler inputs are surfaced
    so the controller can establish those capabilities before borrowing a
    slot. This keeps a rotating cell from requesting ingredients that neither
    exist nor have a producer.
    """
    if target_count < 1 or target_item not in LINE_RECIPES:
        return ()
    root_missing = max(0, target_count - int(actual_stock.get(target_item, 0)))
    if root_missing == 0:
        return ()
    shortages: dict[str, int] = {}

    def descend(item: str, missing: int, visiting: frozenset[str]) -> None:
        if missing <= 0 or item in visiting:
            return
        if not _temporary_assembler_recipe(item):
            shortages[item] = max(shortages.get(item, 0), missing)
            return
        spec = LINE_RECIPES[item]
        product_amount = max(1, math.floor(float(spec.get("product_amount", 1))))
        crafts = math.ceil(missing / product_amount)
        next_visiting = visiting | {item}
        for ingredient, amount in zip(
            spec["ingredients"], spec["amounts"], strict=True,
        ):
            required = math.ceil(float(amount) * crafts)
            available = int(usable_stock.get(ingredient, 0))
            if available >= required:
                continue
            descend(ingredient, required - available, next_visiting)

    descend(target_item, root_missing, frozenset())
    return tuple(
        MallBootstrapExternalShortage(item, count)
        for item, count in shortages.items()
    )


def active_bootstrap_loans(
    client: RconClient, surface: str, force: str,
) -> tuple[MallBootstrapLoan, ...]:
    """Read active tagged loans from requester sections, including after restart."""
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "local out={};local prefix='" + _LOAN_PREFIX + "';"
        "local function machine_at(x,y) local e=s.find_entities_filtered{position={x,y},"
        "radius=0.4,force=f,limit=1}[1];if not e then return '-' end;"
        "local ok,r=pcall(function() return e.get_recipe() end);"
        "local name=e.type=='entity-ghost' and e.ghost_name or e.name;"
        "return name..','..(ok and r and r.name or '-') end;"
        "for _,c in pairs(s.find_entities_filtered{name='requester-chest',force=f}) do "
        "local ok,sections=pcall(function() return c.get_logistic_sections() end);"
        "if ok and sections then for _,section in pairs(sections.sections) do "
        "local g=section.group or '';if string.sub(g,1,#prefix)==prefix then "
        "local slot=section.get_slot(1);if slot and slot.value then "
        "out[#out+1]=g..'|'..c.position.x..'|'..c.position.y..'|'"
        "..machine_at(c.position.x-3,c.position.y)..'|'"
        "..machine_at(c.position.x+3,c.position.y) end end end end end;"
        "rcon.print(table.concat(out,';'))"
    )
    raw = client.command("/sc " + lua).strip()
    loans: list[MallBootstrapLoan] = []
    for record in raw.split(";"):
        if not record:
            continue
        group, raw_x, raw_y, left_state, right_state = record.split("|", 4)
        def _parse_side_state(state: str) -> tuple[str, str]:
            if "," in state:
                return state.split(",", 1)
            return "assembling-machine-1", state

        left_machine, left_recipe = _parse_side_state(left_state)
        right_machine, right_recipe = _parse_side_state(right_state)
        parsed = parse_bootstrap_loan_group(group)
        if parsed is None:
            continue
        (
            original, target, count, spare, side, step, step_target, baseline,
            required, minimum, completed,
        ) = parsed
        loans.append(MallBootstrapLoan(
            original_recipe=original,
            target_item=target,
            target_count=count,
            spare_target_count=spare,
            side=side,
            requester_position=(float(raw_x), float(raw_y)),
            current_recipe=left_recipe if side == "left" else right_recipe,
            step_recipe=step,
            step_target_count=step_target,
            step_baseline_finished=baseline,
            step_required_crafts=required,
            step_minimum_crafts=minimum,
            completed_step_targets=completed,
            machine_name=left_machine if side == "left" else right_machine,
        ))
    return tuple(loans)


def _as_configuration(action: dict) -> dict:
    return {**action, "action_type": "configure_entity"}


def bootstrap_loan_plan(
    loan: MallBootstrapLoan, step: MallBootstrapStep, *,
    baseline_finished: int = 0, minimum_crafts: int | None = None,
    previous_group: str | None = None,
) -> dict:
    """Retarget the borrowed machine, requester ingredients, gate, and output."""
    active = loan.starting_step(
        step.recipe, step.target_count, baseline_finished, step.crafts,
        step.crafts if minimum_crafts is None else minimum_crafts,
    )
    spec = LINE_RECIPES[step.recipe]
    requests = recipe_group_requests(spec["ingredients"], spec["amounts"])
    machine = {
        "action_type": "configure_entity",
        "entity": loan.machine_name,
        "position": {"x": loan.machine_position[0], "y": loan.machine_position[1]},
        "recipe": step.recipe,
        "logistic_condition": stock_gate(step.recipe, step.target_count),
    }
    requester = {
        "action_type": "configure_entity",
        "entity": "requester-chest",
        "position": {
            "x": loan.requester_position[0], "y": loan.requester_position[1],
        },
        "logistic_sections": [{
            "group": active.group,
            "requests": requests,
            "multiplier": max(1, step.crafts),
        }],
        # Stop requesting the borrowed recipe before its assembler changes.
        # The executor processes actions in order and returns any ingredient
        # already held by the feeding inserter to this chest before set_recipe.
        "clear_logistic_groups": [
            recipe_group_name(loan.original_recipe),
            recipe_group_name(loan.original_recipe, loan.side),
        ],
    }
    if active.group != loan.group:
        requester["clear_logistic_groups"].append(loan.group)
    if previous_group and previous_group not in requester["clear_logistic_groups"]:
        requester["clear_logistic_groups"].append(previous_group)
    provider = generate_mall_provider_limit_update(
        step.recipe, loan.provider_position, step.target_count,
        # A borrowed provider can still contain several stacks from its
        # original recipe. Lowering its bar to the new recipe's one-slot target
        # can strand the bar behind those stacks and block every new output.
        # Keep it open only during the loan; restoration reapplies the normal
        # cap for the original recipe.
        fill_chest=True,
    )["phases"][0]["actions"][0]
    return {"phases": [{
        "name": f"bootstrap_loan_{step.recipe}",
        "actions": [requester, machine, _as_configuration(provider)],
    }]}


def restore_bootstrap_loan_plan(loan: MallBootstrapLoan) -> dict:
    """Restore the original recipe and request group after the seed is stocked."""
    spec = LINE_RECIPES[loan.original_recipe]
    requester_section = {
        "group": recipe_group_name(loan.original_recipe, loan.side),
        "requests": recipe_group_requests(spec["ingredients"], spec["amounts"]),
        "multiplier": request_multiplier(loan.machine_name, spec["craft_time"]),
    }
    provider = generate_mall_provider_limit_update(
        loan.original_recipe, loan.provider_position, 1,
    )["phases"][0]["actions"][0]
    return {"phases": [{
        "name": f"restore_bootstrap_loan_{loan.original_recipe}",
        "actions": [
            {
                "action_type": "configure_entity",
                "entity": "requester-chest",
                "position": {
                    "x": loan.requester_position[0], "y": loan.requester_position[1],
                },
                # Original mall groups are shared by every matching cell on
                # this side. Leave them intact while the unique loan group is
                # removed, or restoring one borrower would rewrite its peers.
                "clear_logistic_groups": [loan.group],
                "logistic_sections": [requester_section],
            },
            {
                "action_type": "configure_entity",
                "entity": loan.machine_name,
                "position": {
                    "x": loan.machine_position[0], "y": loan.machine_position[1],
                },
                "recipe": loan.original_recipe,
                "clear_logistic_condition": True,
            },
            _as_configuration(provider),
        ],
    }]}


def promote_bootstrap_loan_plan(
    loan: MallBootstrapLoan, *, stock_target: int,
    clear_original_groups: bool = False,
) -> dict:
    """Keep a completed borrowed cell as the permanent target producer.

    Promotion reuses the assembler, requester, inserters, power, and provider
    already owned by a demand slot. The provider remains open because paired
    bootstrap halves may share it; the assembler's stock condition bounds
    production without imposing an inventory bar behind older products.
    """
    spec = LINE_RECIPES[loan.target_item]
    requester_section = {
        "group": recipe_group_name(loan.target_item, loan.side),
        "requests": recipe_group_requests(spec["ingredients"], spec["amounts"]),
        "multiplier": request_multiplier(loan.machine_name, spec["craft_time"]),
    }
    provider = generate_mall_provider_limit_update(
        loan.target_item, loan.provider_position, stock_target,
        fill_chest=True,
    )["phases"][0]["actions"][0]
    clear_groups = [loan.group]
    if clear_original_groups:
        # A core promotion may be entered from a legacy/no-step loan whose
        # unique tag is not the only request group still on the shared chest.
        # Remove this borrower's base and side-labelled groups as well, while
        # leaving a paired companion's side group untouched.
        for group in (
            recipe_group_name(loan.original_recipe),
            recipe_group_name(loan.original_recipe, loan.side),
        ):
            if group not in clear_groups:
                clear_groups.append(group)
    return {"phases": [{
        "name": f"promote_bootstrap_loan_{loan.target_item}",
        "actions": [
            {
                "action_type": "configure_entity",
                "entity": "requester-chest",
                "position": {
                    "x": loan.requester_position[0],
                    "y": loan.requester_position[1],
                },
                "clear_logistic_groups": clear_groups,
                "logistic_sections": [requester_section],
            },
            {
                "action_type": "configure_entity",
                "entity": loan.machine_name,
                "position": {
                    "x": loan.machine_position[0],
                    "y": loan.machine_position[1],
                },
                "recipe": loan.target_item,
                "logistic_condition": stock_gate(
                    loan.target_item, max(1, stock_target),
                ),
            },
            _as_configuration(provider),
        ],
    }]}
