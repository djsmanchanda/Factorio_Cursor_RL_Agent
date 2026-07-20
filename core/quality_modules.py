# Path: core/quality_modules.py
# Purpose: Deterministic data tables + validation helpers for Factorio quality
# tiers and modules (wiki-derived external knowledge, docs/21). Data-only per
# docs/19/docs/20: never overrides planner standards, only informs them.

from __future__ import annotations

# --- Quality tiers (wiki.factorio.com/Quality) ---------------------------
# Effects are per-strength-point and additive across the tier's strength.
QUALITY_TIERS: dict[str, int] = {
    "normal": 0,
    "uncommon": 1,
    "rare": 2,
    "epic": 3,
    "legendary": 5,
}
QUALITY_TIER_ORDER: list[str] = ["normal", "uncommon", "rare", "epic", "legendary"]

# Per-entity-class quality effect per strength point. Belts/walls are
# health-only (no throughput effect) and intentionally omitted here as
# planner-irrelevant; callers should not look them up for throughput math.
QUALITY_ENTITY_EFFECTS: dict[str, dict[str, float]] = {
    "assembling-machine": {"crafting_speed": 0.30},
    "inserter": {"rotation_speed": 0.30},
    "electric-pole": {"supply_reach_tiles": 1.0, "wire_reach_tiles": 2.0},
    "beacon": {"power": -0.1667},
    "module": {"positive_effect": 0.30},
}

# --- Modules (wiki.factorio.com/Module) -----------------------------------
# Effects keyed by module family -> tier (1/2/3) -> stat deltas.
MODULE_EFFECTS: dict[str, dict[int, dict[str, float]]] = {
    "speed-module": {
        1: {"speed": 0.20, "energy": 0.50},
        2: {"speed": 0.30, "energy": 0.60},
        3: {"speed": 0.50, "energy": 0.70},
    },
    "productivity-module": {
        1: {"productivity": 0.04, "energy": 0.40, "speed": -0.05},
        2: {"productivity": 0.06, "energy": 0.60, "speed": -0.10},
        3: {"productivity": 0.10, "energy": 0.80, "speed": -0.15},
    },
    "efficiency-module": {
        1: {"energy": -0.30},
        2: {"energy": -0.40},
        3: {"energy": -0.50},
    },
    "quality-module": {
        1: {"quality_chance": 0.01, "speed": -0.05},
        2: {"quality_chance": 0.02, "speed": -0.05},
        3: {"quality_chance": 0.025, "speed": -0.05},
    },
}

# Machine properties (speed/energy/pollution) cannot drop below this fraction
# of their original value, regardless of module stacking.
MODULE_EFFECT_FLOOR = 0.20  # i.e. delta cannot push a multiplier below 0.20

# Module slot counts, verified live against Factorio 2.0.77 prototypes
# (module_inventory_size via RCON, 2026-07-18). assembling-machine-1 truly
# has zero slots, so the overflow rule alone rejects modules on it.
MODULE_SLOTS: dict[str, int] = {
    "assembling-machine-1": 0,
    "assembling-machine-2": 2,
    "assembling-machine-3": 4,
    "electric-furnace": 2,
    "beacon": 2,
}
# Mechanism retained for future machines whose slot counts have not been
# verified against live prototypes yet.
UNVERIFIED_SLOT_MACHINES: frozenset[str] = frozenset()

# Recipes producing intermediate products in our LINE_RECIPES world (see
# planners/local_layout_planner.py). Science packs accept productivity in
# real Factorio, so automation-science-pack is included alongside the
# classic intermediates.
INTERMEDIATE_RECIPES: frozenset[str] = frozenset({
    "iron-gear-wheel",
    "copper-cable",
    "iron-stick",
    "electronic-circuit",
    "iron-plate",
    "copper-plate",
    "automation-science-pack",
})


def quality_multiplier(entity_class: str, tier: str) -> float:
    """Return the multiplier (1 + effect_fraction) for a quality-tier
    entity's *first* (primary) numeric effect, e.g. assembling-machine at
    rare = 1 + 0.30*2 = 1.6. For entities with multiple effect keys, prefer
    quality_effect_value for a specific stat."""
    value = quality_effect_value(entity_class, tier, _primary_effect_key(entity_class))
    return 1.0 + value


def quality_effect_value(entity_class: str, tier: str, effect_key: str) -> float:
    """Total additive effect fraction for one stat at the given tier
    (per-strength effect * tier strength)."""
    if entity_class not in QUALITY_ENTITY_EFFECTS:
        raise ValueError(f"Unknown quality entity class: {entity_class}")
    if tier not in QUALITY_TIERS:
        raise ValueError(f"Unknown quality tier: {tier}")
    effects = QUALITY_ENTITY_EFFECTS[entity_class]
    if effect_key not in effects:
        raise ValueError(f"Entity class {entity_class} has no '{effect_key}' quality effect")
    return effects[effect_key] * QUALITY_TIERS[tier]


def _primary_effect_key(entity_class: str) -> str:
    if entity_class not in QUALITY_ENTITY_EFFECTS:
        raise ValueError(f"Unknown quality entity class: {entity_class}")
    return next(iter(QUALITY_ENTITY_EFFECTS[entity_class]))


def _clamp_multiplier(multiplier: float) -> float:
    return max(MODULE_EFFECT_FLOOR, multiplier)


def apply_modules(base_speed: float, base_energy: float, modules: list[str]) -> dict:
    """Apply a list of module names (e.g. "speed-module-2") to base speed and
    energy, accumulating productivity and quality_chance, enforcing the 20%
    floor on speed/energy multipliers."""
    speed_delta = 0.0
    energy_delta = 0.0
    productivity = 0.0
    quality_chance = 0.0

    for module in modules:
        family, tier = _parse_module_name(module)
        effects = MODULE_EFFECTS[family][tier]
        speed_delta += effects.get("speed", 0.0)
        energy_delta += effects.get("energy", 0.0)
        productivity += effects.get("productivity", 0.0)
        quality_chance += effects.get("quality_chance", 0.0)

    speed_multiplier = _clamp_multiplier(1.0 + speed_delta)
    energy_multiplier = _clamp_multiplier(1.0 + energy_delta)

    return {
        "speed": base_speed * speed_multiplier,
        "energy": base_energy * energy_multiplier,
        "productivity": productivity,
        "quality_chance": quality_chance,
    }


def _parse_module_name(module: str) -> tuple[str, int]:
    # Expected form "<family>-<tier>", e.g. "speed-module-2".
    parts = module.rsplit("-", 1)
    if len(parts) != 2 or not parts[1].isdigit():
        raise ValueError(f"Malformed module name: {module!r} (expected '<family>-module-<tier>')")
    family, tier_str = parts[0], parts[1]
    tier = int(tier_str)
    if family not in MODULE_EFFECTS or tier not in MODULE_EFFECTS[family]:
        raise ValueError(f"Unknown module: {module!r}")
    return family, tier


def validate_module_config(machine: str, recipe: str, modules: list[str], is_intermediate: bool | None = None) -> None:
    """Raise ValueError on: slot overflow, productivity module on a
    non-intermediate recipe, or any module placed in a machine with 0/unknown
    slots. `is_intermediate` overrides the INTERMEDIATE_RECIPES lookup when
    provided (callers outside the LINE_RECIPES world must pass it)."""
    if not modules:
        return

    slots = MODULE_SLOTS.get(machine)
    if machine in UNVERIFIED_SLOT_MACHINES:
        raise ValueError(
            f"Module slot count for '{machine}' is unverified (NEEDS_VERIFICATION); "
            "refusing to place modules until confirmed"
        )
    if slots is None:
        raise ValueError(f"Unknown machine for module slots: {machine}")
    if slots == 0:
        raise ValueError(f"Machine '{machine}' has 0 module slots")
    if len(modules) > slots:
        raise ValueError(f"Module overflow: {len(modules)} modules exceed {slots} slots on '{machine}'")

    intermediate = INTERMEDIATE_RECIPES.__contains__(recipe) if is_intermediate is None else is_intermediate
    for module in modules:
        family, _tier = _parse_module_name(module)
        if family == "productivity-module":
            if machine == "beacon":
                raise ValueError("Productivity modules are never allowed in beacons")
            if not intermediate:
                raise ValueError(
                    f"Productivity module '{module}' requires an intermediate-product recipe; "
                    f"'{recipe}' is not in INTERMEDIATE_RECIPES"
                )


def validate_uniform_quality(recipe_tier: str, ingredient_tiers: dict[str, str]) -> None:
    """Jam guard (user-mandated safety rule): a recipe crafting at
    `recipe_tier` requires ALL item ingredients at exactly that tier (not
    minimum). Mixed-quality items on a shared line jam production
    irrecoverably, so quality production requires dedicated, sorted lines
    per tier. Fluids carry no quality and are exempt (callers should not
    include fluid ingredients in `ingredient_tiers`)."""
    if recipe_tier not in QUALITY_TIERS:
        raise ValueError(f"Unknown quality tier: {recipe_tier}")
    mismatches = {
        item: tier for item, tier in ingredient_tiers.items()
        if tier != recipe_tier
    }
    if mismatches:
        raise ValueError(
            f"Ingredient quality mismatch for recipe tier '{recipe_tier}': {mismatches} "
            "(quality lines require uniform, exactly-matching ingredient tiers)"
        )
