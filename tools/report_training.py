# Path: tools/report_training.py
# Purpose: Summarize durable episode outcomes and local MoE runtime measurements.

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def training_summary(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"training database not found: {path}")
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        episode_rows = connection.execute(
            "SELECT status,COUNT(*) count FROM episodes GROUP BY status ORDER BY status"
        ).fetchall()
        model_rows = connection.execute(
            "SELECT model,model_hash,n_cpu_moe,COUNT(*) samples,AVG(elapsed_ms) mean_elapsed_ms,"
            "MAX(peak_ram_mb) peak_ram_mb,MAX(peak_vram_mb) peak_vram_mb "
            "FROM llm_runtime_samples GROUP BY model,model_hash,n_cpu_moe ORDER BY samples DESC"
        ).fetchall()
        return {
            "episodes": {row["status"]: row["count"] for row in episode_rows},
            "llm_runtime": [dict(row) for row in model_rows],
            "expert_routing_available": False,
            "expert_routing_note": "stock llama.cpp records layer offload, not per-expert routing counts",
        }
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report isolated RL training evidence.")
    parser.add_argument("--database", type=Path, default=Path("data/training/experience.db"))
    args = parser.parse_args(argv)
    print(json.dumps(training_summary(args.database), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
