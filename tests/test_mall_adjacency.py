# Path: tests/test_mall_adjacency.py | Purpose: Prove that mall expansion chooses the nearest free slot beside the committed bank.

from __future__ import annotations

from orchestrator import mall_builder


_REFERENCE = (3.0, -1.0)


def test_new_cells_are_chosen_adjacent_to_the_committed_bank(monkeypatch) -> None:
    origins = mall_builder._cell_origins(_REFERENCE)
    committed = origins[:2]
    states = {origin: ("-", "-", "-") for origin in origins}
    for origin in committed:
        states[origin] = ("iron-gear-wheel", "copper-cable", "requester-chest")
    monkeypatch.setattr(mall_builder, "_district_state", lambda *_a: states)
    monkeypatch.setattr(mall_builder, "_side_clear", lambda *_a: True)
    monkeypatch.setattr(mall_builder.live_base, "area_clear", lambda *_a, **_k: True)
    monkeypatch.setattr(
        mall_builder.resource_patches, "box_has_reserved_patch",
        lambda *_a, **_k: False,
    )

    origin, side = mall_builder._choose_slot(
        object(), "nauvis", "electronic-circuit", _REFERENCE,
    )

    assert (origin, side) == ((35, 43), "left")
    assert origin not in committed
