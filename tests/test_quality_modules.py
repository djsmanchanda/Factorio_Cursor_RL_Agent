# Path: tests/test_quality_modules.py
# Purpose: Deterministic tests for core/quality_modules.py — tier math,
# module stacking with floor clamp, productivity/slot validation, and the
# uniform-quality jam guard.

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.quality_modules import (
    apply_modules,
    quality_effect_value,
    quality_multiplier,
    validate_module_config,
    validate_uniform_quality,
)


# --- Quality tier math ------------------------------------------------------

def test_quality_multiplier_assembler_rare():
    # rare strength = 2, assembler crafting_speed effect = +30%/strength
    assert quality_multiplier("assembling-machine", "rare") == pytest.approx(1.6)


def test_quality_multiplier_normal_is_identity():
    assert quality_multiplier("assembling-machine", "normal") == pytest.approx(1.0)


def test_quality_multiplier_legendary_inserter():
    # legendary strength = 5, inserter rotation_speed +30%/strength -> +150%
    assert quality_multiplier("inserter", "legendary") == pytest.approx(2.5)


def test_quality_effect_value_electric_pole_reach():
    assert quality_effect_value("electric-pole", "epic", "supply_reach_tiles") == pytest.approx(3.0)
    assert quality_effect_value("electric-pole", "epic", "wire_reach_tiles") == pytest.approx(6.0)


def test_quality_effect_value_beacon_power_is_negative():
    assert quality_effect_value("beacon", "uncommon", "power") == pytest.approx(-0.1667)


def test_quality_unknown_tier_raises():
    with pytest.raises(ValueError):
        quality_multiplier("assembling-machine", "mythic")


def test_quality_unknown_entity_class_raises():
    with pytest.raises(ValueError):
        quality_multiplier("transport-belt", "rare")


# --- Module stacking + floor clamp -----------------------------------------

def test_single_speed_module_2():
    result = apply_modules(base_speed=1.0, base_energy=1.0, modules=["speed-module-2"])
    assert result["speed"] == pytest.approx(1.30)
    assert result["energy"] == pytest.approx(1.60)


def test_efficiency_modules_clamp_at_floor():
    # 4x efficiency-module-3 (-50% each) = -200% raw, clamped to the 20% floor.
    result = apply_modules(base_speed=1.0, base_energy=100.0, modules=["efficiency-module-3"] * 4)
    assert result["energy"] == pytest.approx(20.0)  # 100 * 0.20 floor
    assert result["speed"] == pytest.approx(1.0)  # unaffected


def test_speed_modules_do_not_clamp_below_floor_when_unnecessary():
    result = apply_modules(base_speed=1.0, base_energy=1.0, modules=["speed-module-1", "speed-module-1"])
    assert result["speed"] == pytest.approx(1.40)


def test_productivity_and_quality_chance_accumulate():
    result = apply_modules(
        base_speed=1.0, base_energy=1.0,
        modules=["productivity-module-3", "quality-module-2"],
    )
    assert result["productivity"] == pytest.approx(0.10)
    assert result["quality_chance"] == pytest.approx(0.02)
    # productivity-3 speed -0.15, quality-2 speed -0.05 -> combined -0.20
    assert result["speed"] == pytest.approx(0.80)


def test_malformed_module_name_raises():
    with pytest.raises(ValueError):
        apply_modules(1.0, 1.0, ["speed-turbo"])


def test_unknown_module_family_raises():
    with pytest.raises(ValueError):
        apply_modules(1.0, 1.0, ["nuclear-module-1"])


# --- validate_module_config: slots, productivity restriction ---------------

def test_slot_overflow_raises():
    with pytest.raises(ValueError, match="overflow"):
        validate_module_config(
            "assembling-machine-2", "iron-gear-wheel",
            ["speed-module-1", "speed-module-1", "speed-module-1"],
        )


def test_slots_ok_within_limit():
    validate_module_config("assembling-machine-2", "iron-gear-wheel", ["speed-module-1", "speed-module-1"])


def test_productivity_on_intermediate_recipe_ok():
    validate_module_config("assembling-machine-3", "electronic-circuit", ["productivity-module-2"])


def test_productivity_on_non_intermediate_recipe_raises():
    with pytest.raises(ValueError, match="intermediate"):
        validate_module_config("assembling-machine-3", "landfill", ["productivity-module-1"])


def test_productivity_in_beacon_always_raises():
    with pytest.raises(ValueError, match="beacon"):
        validate_module_config("beacon", "electronic-circuit", ["productivity-module-1"])


def test_is_intermediate_override_allows_non_table_recipe():
    validate_module_config(
        "electric-furnace", "some-future-intermediate",
        ["productivity-module-1"], is_intermediate=True,
    )


def test_module_in_zero_slot_machine_raises():
    with pytest.raises(ValueError):
        validate_module_config("assembling-machine-1", "iron-gear-wheel", ["speed-module-1"])


def test_module_in_unknown_machine_raises():
    with pytest.raises(ValueError):
        validate_module_config("rocket-silo", "iron-gear-wheel", ["speed-module-1"])


def test_no_modules_never_raises_even_for_unverified_machine():
    validate_module_config("assembling-machine-1", "iron-gear-wheel", [])


# --- validate_uniform_quality: the jam guard --------------------------------

def test_uniform_quality_passes_when_all_match():
    validate_uniform_quality("rare", {"iron-plate": "rare", "copper-plate": "rare"})


def test_uniform_quality_fails_on_any_mismatch():
    with pytest.raises(ValueError, match="mismatch"):
        validate_uniform_quality("rare", {"iron-plate": "rare", "copper-plate": "normal"})


def test_uniform_quality_fails_when_higher_tier_present_not_just_lower():
    # Exact match required, not minimum -- a higher-tier ingredient also fails.
    with pytest.raises(ValueError):
        validate_uniform_quality("uncommon", {"iron-plate": "epic"})


def test_uniform_quality_unknown_tier_raises():
    with pytest.raises(ValueError):
        validate_uniform_quality("mythic", {"iron-plate": "mythic"})
