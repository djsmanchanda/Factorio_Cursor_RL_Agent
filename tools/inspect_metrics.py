# Path: tools/inspect_metrics.py
# Purpose: Inspect derived metrics from a snapshot without making decisions.

import json
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))

from core.factory_graph import FactoryGraph
from core.metrics import compute_all_metrics
from tools.validate_snapshot import validate_snapshot_file


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python tools/inspect_metrics.py <snapshot.json>")
        return 2

    schema_path = repo_root / "schemas" / "snapshot.schema.json"
    snapshot_path = Path(sys.argv[1]).resolve()

    errors = validate_snapshot_file(snapshot_path, schema_path)
    if errors:
        print("Snapshot validation FAILED:")
        for error in errors:
            path = "/".join(str(part) for part in error.path) if error.path else "<root>"
            print(f"- {path}: {error.message}")
        return 1

    with snapshot_path.open("r", encoding="utf-8") as handle:
        snapshot = json.load(handle)
    graph = FactoryGraph(snapshot)

    bot_metrics, prod_metrics, power_metrics = compute_all_metrics(graph)

    print("Bots:")
    print(f"  logistic: {bot_metrics.active_logistic_bots}")
    print(f"  construction: {bot_metrics.active_construction_bots}")
    print(f"  total: {bot_metrics.total_bots}")
    if bot_metrics.bot_density_per_tile is not None:
        print(f"  density: {bot_metrics.bot_density_per_tile:.6f} bots/tile")
    if bot_metrics.estimated_bot_trip_span is not None:
        print(f"  estimated_trip_span: {bot_metrics.estimated_bot_trip_span:.2f}")

    print("")
    print("Production:")
    if prod_metrics.assemblers_per_recipe:
        print("  assemblers_per_recipe:")
        for recipe in sorted(prod_metrics.assemblers_per_recipe):
            print(f"    {recipe}: {prod_metrics.assemblers_per_recipe[recipe]}")
    if prod_metrics.miners_by_name:
        print("  miners_by_name:")
        for name in sorted(prod_metrics.miners_by_name):
            print(f"    {name}: {prod_metrics.miners_by_name[name]}")
    if prod_metrics.furnaces_by_name:
        print("  furnaces_by_name:")
        for name in sorted(prod_metrics.furnaces_by_name):
            print(f"    {name}: {prod_metrics.furnaces_by_name[name]}")
    print(f"  labs: {prod_metrics.labs_count}")

    print("")
    print("Power:")
    if power_metrics.generators_by_name:
        print("  generators_by_name:")
        for name in sorted(power_metrics.generators_by_name):
            print(f"    {name}: {power_metrics.generators_by_name[name]}")
    if power_metrics.consumers_by_name:
        print("  consumers_by_name:")
        for name in sorted(power_metrics.consumers_by_name):
            print(f"    {name}: {power_metrics.consumers_by_name[name]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
