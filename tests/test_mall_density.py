# Path: tests/test_mall_density.py
# Purpose: Prove the denser paired-mall grid remains collision-free.

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
from orchestrator import mall_builder  # noqa: E402
from orchestrator.mall_builder import _CELL_PITCH  # noqa: E402
from planners.mall_layout import generate_paired_mall_layout  # noqa: E402
from planners.plan_validation import validate_no_collisions  # noqa: E402
from planners.recipe_data import LINE_RECIPES  # noqa: E402


def test_dense_mall_pitch_keeps_adjacent_cells_clear() -> None:
    """The tighter 3x3 district must not overlap machines or substations.

    Each cell is tested with a different side assignment so this covers the
    allocator's normal left/right alternation, not just one symmetric layout.
    """
    assert _CELL_PITCH == (11, 6)
    recipes = ("copper-cable", "iron-gear-wheel", "electronic-circuit")
    sides = ("left", "right", "right", "left", "left", "right", "right", "left", "right")
    plans = []
    for index, side in enumerate(sides):
        row, column = divmod(index, 3)
        origin = (column * _CELL_PITCH[0], row * _CELL_PITCH[1])
        recipe = recipes[index % len(recipes)]
        spec = LINE_RECIPES[recipe]
        plans.append((
            f"cell-{index}",
            generate_paired_mall_layout(
                recipe,
                spec["machine"],
                spec["ingredients"],
                spec["amounts"],
                origin,
                side,
                stock_target=6,
                product_amount=spec.get("product_amount", 1),
                craft_time=spec["craft_time"],
                set_recipe=spec.get("set_recipe", True),
            ),
        ))

    validate_no_collisions(plans)


def test_cables_split_between_circuits_and_transport_belts(monkeypatch) -> None:
    reference = (3.0, -1.0)
    circuit_cell, belt_cell, *_rest = mall_builder._cell_origins(reference)
    states = {
        origin: ("-", "-", "-")
        for origin in mall_builder._cell_origins(reference)
    }
    states[circuit_cell] = ("copper-cable", "-", "requester-chest")
    states[belt_cell] = ("copper-cable", "-", "requester-chest")
    monkeypatch.setattr(mall_builder, "_district_state", lambda *_args: states)
    monkeypatch.setattr(mall_builder, "_side_clear", lambda *_args: True)
    monkeypatch.setattr(
        mall_builder.live_base, "area_clear", lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        mall_builder.resource_patches,
        "box_has_reserved_patch",
        lambda *_args, **_kwargs: False,
    )

    assert mall_builder._choose_slot(
        object(), "nauvis", "electronic-circuit", reference,
    ) == (circuit_cell, "right")
    states[circuit_cell] = (
        "copper-cable", "electronic-circuit", "requester-chest",
    )
    assert mall_builder._choose_slot(
        object(), "nauvis", "transport-belt", reference,
    ) == (belt_cell, "right")
    states = {
        origin: ("-", "-", "-")
        for origin in mall_builder._cell_origins(reference)
    }
    states[circuit_cell] = ("copper-cable", "-", "requester-chest")
    monkeypatch.setattr(mall_builder, "_district_state", lambda *_args: states)

    assert mall_builder._choose_slot(
        object(), "nauvis", "copper-cable", reference,
    ) == (belt_cell, "left")
