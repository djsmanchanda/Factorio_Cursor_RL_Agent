# Path: tools/starter_kit_audit.py
# Purpose: Report which hand-placed starter-kit items the base can now make for itself, so the kit can be withdrawn item by item instead of all at once.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import live_base  # noqa: E402
from orchestrator.game_bridge import GameBridge, load_json  # noqa: E402
from planners.recipe_data import LINE_RECIPES, install_catalog_line_recipes  # noqa: E402
from tools.rcon_client import RconClient  # noqa: E402

# The kit, grouped by how hard it is to do without. Withdrawal order runs down
# this list: an item is only safe to remove once the base can make it AND
# everything it is made of.
KIT_TIERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("intermediates", (
        "iron-plate", "copper-plate", "steel-plate", "iron-gear-wheel",
        "copper-cable", "electronic-circuit", "advanced-circuit", "iron-stick",
        "pipe", "stone-brick",
    )),
    ("belts and inserters", (
        "transport-belt", "fast-transport-belt", "underground-belt",
        "inserter", "fast-inserter", "bulk-inserter",
    )),
    ("chests and poles", (
        "wooden-chest", "iron-chest", "steel-chest", "small-electric-pole",
        "medium-electric-pole", "substation", "pipe-to-ground", "storage-tank",
    )),
    ("machines", (
        "assembling-machine-1", "assembling-machine-2", "electric-furnace",
        "electric-mining-drill", "chemical-plant", "oil-refinery", "pumpjack",
        "offshore-pump", "lab",
    )),
    ("logistics network", (
        "passive-provider-chest", "requester-chest", "active-provider-chest",
        "storage-chest", "buffer-chest", "roboport",
    )),
    ("robots", (
        "flying-robot-frame", "construction-robot", "logistic-robot",
    )),
)


def _producible(item: str, seen: frozenset[str] = frozenset()) -> tuple[bool, str]:
    """Whether the agent could build `item` from raw ore, and what stops it.

    Knowing the recipe is not enough: a recipe whose own ingredients are
    unbuildable is just a longer way of depending on the kit.
    """
    if item in seen:
        return False, f"circular through {item}"
    spec = LINE_RECIPES.get(item)
    if spec is None:
        return (True, "mined") if item in {
            "iron-ore", "copper-ore", "coal", "stone", "water", "wood",
        } else (False, "no recipe the agent can execute")
    for ingredient in spec["ingredients"]:
        ok, why = _producible(ingredient, seen | {item})
        if not ok:
            return False, f"needs {ingredient} ({why})"
    return True, "buildable"


def audit(stock: dict[str, int], built: dict[str, int]) -> list[dict]:
    """One row per kit item: can we make it, and are we already making it."""
    rows = []
    for tier, items in KIT_TIERS:
        for item in items:
            ok, why = _producible(item)
            rows.append({
                "tier": tier, "item": item, "producible": ok, "reason": why,
                "stock": stock.get(item, 0), "producing": built.get(item, 0),
            })
    return rows


def _render(rows: list[dict]) -> str:
    out: list[str] = []
    tier = None
    for row in rows:
        if row["tier"] != tier:
            tier = row["tier"]
            out.append(f"\n=== {tier} ===")
        if not row["producible"]:
            mark, note = "BLOCKED ", row["reason"]
        elif row["producing"]:
            mark, note = "SELF-MADE", f"{row['producing']} machine(s) running"
        else:
            mark, note = "READY   ", "buildable, but nothing is making it yet"
        out.append(f"  {mark} {row['item']:26} stock={row['stock']:<6} {note}")
    blocked = [r["item"] for r in rows if not r["producible"]]
    made = [r["item"] for r in rows if r["producible"] and r["producing"]]
    out.append(
        f"\n{len(made)} item(s) the base actually produces; "
        f"{len(blocked)} the agent still cannot build at all."
    )
    if blocked:
        out.append("Cannot be withdrawn until the agent learns to build them:")
        out.extend(f"  - {item}" for item in blocked)
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--surface", default="nauvis")
    parser.add_argument("--force", default="player")
    parser.add_argument("--rcon-host", default="127.0.0.1")
    parser.add_argument("--rcon-port", type=int, default=27017)
    parser.add_argument("--rcon-password", default="")
    parser.add_argument("--script-output", default="")
    parser.add_argument("--json", action="store_true", help="emit rows as JSON")
    args = parser.parse_args()

    client = RconClient(args.rcon_host, args.rcon_port, args.rcon_password)
    bridge = GameBridge(
        script_output=Path(args.script_output), host=args.rcon_host,
        port=args.rcon_port, password=args.rcon_password,
    )
    try:
        install_catalog_line_recipes(
            load_json(bridge.export_recipe_catalog(force=args.force))
        )
        stock = live_base.available_items(client, args.surface, args.force)
        built = {}
        for item, spec in LINE_RECIPES.items():
            line = live_base.find_line(
                client, args.surface, args.force, item, spec["machine"],
            )
            if line is not None and line.machine_count:
                built[item] = line.machine_count
        rows = audit(stock, built)
    finally:
        client.close()
        bridge.close()

    print(json.dumps(rows, indent=2) if args.json else _render(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
