# Path: tests/test_power_corridor_preference.py | Purpose: Protect the stocked power corridor choice: fewest poles, then least land.

from orchestrator.stage_services import _corridor_preference


def test_equal_length_corridors_end_in_the_smaller_pole():
    big = [("big-electric-pole", (52.0, 30.0)), ("big-electric-pole", (58.0, 35.0))]
    medium = [("big-electric-pole", (52.0, 30.0)), ("medium-electric-pole", (57.5, 35.5))]
    assert min([big, medium], key=_corridor_preference) == medium
    shorter = [("big-electric-pole", (58.0, 35.0))]
    assert min([medium, shorter], key=_corridor_preference) == shorter  # fewer poles still wins
