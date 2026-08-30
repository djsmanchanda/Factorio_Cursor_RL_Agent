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
    step_baseline_finished: int | None = None
    step_required_crafts: int | None = None
    step_minimum_crafts: int | None = None

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
        return (
            f"{_LOAN_V3_PREFIX}{self.original_recipe}:{self.target_item}:"
            f"{self.target_count}:{self.production_target}:{self.side}:"
            f"{self.step_recipe}:{self.step_baseline_finished}:"
            f"{self.step_required_crafts}:{self.step_minimum_crafts}"
        )

    def starting_step(
        self, recipe: str, baseline_finished: int, required_crafts: int,
        minimum_crafts: int,
    ) -> "MallBootstrapLoan":
        return replace(
            self, current_recipe=recipe, step_recipe=recipe,
            spare_target_count=self.production_target,
            step_baseline_finished=max(0, int(baseline_finished)),
            step_required_crafts=max(1, int(required_crafts)),
            step_minimum_crafts=max(0, int(minimum_crafts)),
        )

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
    int | None,
] | None:
    if group.startswith(_LOAN_V1_PREFIX):
        fields = group[len(_LOAN_V1_PREFIX):].split(":")
        if len(fields) != 4:
            return None
        original, target, raw_count, side = fields
        raw_spare = None
        step, raw_baseline, raw_required, raw_minimum = None, None, None, None
    elif group.startswith(_LOAN_V2_PREFIX):
        fields = group[len(_LOAN_V2_PREFIX):].split(":")
        if len(fields) != 7:
            return None
        original, target, raw_count, side, step, raw_baseline, raw_required = fields
        raw_spare, raw_minimum = None, None
    elif group.startswith(_LOAN_V3_PREFIX):
        fields = group[len(_LOAN_V3_PREFIX):].split(":")
        if len(fields) != 9:
            return None
        (
            original, target, raw_count, raw_spare, side, step,
            raw_baseline, raw_required, raw_minimum,
        ) = fields
    else:
        return None
    try:
        count = int(raw_count)
        spare = int(raw_spare) if raw_spare is not None else None
        baseline = int(raw_baseline) if raw_baseline is not None else None
        required = int(raw_required) if raw_required is not None else None
        minimum = int(raw_minimum) if raw_minimum is not None else None
    except ValueError:
        return None
    if (
        not original or not target or count < 1
        or (spare is not None and spare < count)
        or side not in {"left", "right"}
        or (step is not None and (
            not step or baseline is None or baseline < 0
            or required is None or required < 1
            or (group.startswith(_LOAN_V3_PREFIX)
                and (minimum is None or minimum < 0 or minimum > required))
        ))
    ):
        return None
    return (
        original, target, count, spare, side, step, baseline, required, minimum,
    )


def _temporary_assembler_recipe(item: str) -> bool:
    spec = LINE_RECIPES.get(item)
    return bool(
        spec
        and spec.get("set_recipe", True)
        and spec.get("machine") == "assembling-machine-2"
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


def active_bootstrap_loans(
    client: RconClient, surface: str, force: str,
) -> tuple[MallBootstrapLoan, ...]:
    """Read active tagged loans from requester sections, including after restart."""
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "local out={};local prefix='" + _LOAN_PREFIX + "';"
        "local function recipe_at(x,y) local e=s.find_entities_filtered{position={x,y},"
        "radius=0.4,force=f,limit=1}[1];if not e then return '-' end;"
        "local ok,r=pcall(function() return e.get_recipe() end);"
        "return ok and r and r.name or '-' end;"
        "for _,c in pairs(s.find_entities_filtered{name='requester-chest',force=f}) do "
        "local ok,sections=pcall(function() return c.get_logistic_sections() end);"
        "if ok and sections then for _,section in pairs(sections.sections) do "
        "local g=section.group or '';if string.sub(g,1,#prefix)==prefix then "
        "local slot=section.get_slot(1);if slot and slot.value then "
        "out[#out+1]=g..'|'..c.position.x..'|'..c.position.y..'|'"
        "..recipe_at(c.position.x-3,c.position.y)..'|'"
        "..recipe_at(c.position.x+3,c.position.y) end end end end end;"
        "rcon.print(table.concat(out,';'))"
    )
    raw = client.command("/sc " + lua).strip()
    loans: list[MallBootstrapLoan] = []
    for record in raw.split(";"):
        if not record:
            continue
        group, raw_x, raw_y, left_recipe, right_recipe = record.split("|", 4)
        parsed = parse_bootstrap_loan_group(group)
        if parsed is None:
            continue
        (
            original, target, count, spare, side, step, baseline, required,
            minimum,
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
            step_baseline_finished=baseline,
            step_required_crafts=required,
            step_minimum_crafts=minimum,
        ))
    return tuple(loans)


def _as_configuration(action: dict) -> dict:
    return {**action, "action_type": "configure_entity"}


def bootstrap_loan_plan(
    loan: MallBootstrapLoan, step: MallBootstrapStep, *,
    baseline_finished: int = 0, minimum_crafts: int | None = None,
) -> dict:
    """Retarget the borrowed machine, requester ingredients, gate, and output."""
    active = loan.starting_step(
        step.recipe, baseline_finished, step.crafts,
        step.crafts if minimum_crafts is None else minimum_crafts,
    )
    spec = LINE_RECIPES[step.recipe]
    requests = recipe_group_requests(spec["ingredients"], spec["amounts"])
    machine = {
        "action_type": "configure_entity",
        "entity": "assembling-machine-2",
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
        "multiplier": request_multiplier(spec["machine"], spec["craft_time"]),
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
                "entity": "assembling-machine-2",
                "position": {
                    "x": loan.machine_position[0], "y": loan.machine_position[1],
                },
                "recipe": loan.original_recipe,
                "clear_logistic_condition": True,
            },
            _as_configuration(provider),
        ],
    }]}
