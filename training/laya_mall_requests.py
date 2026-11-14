# Path: training/laya_mall_requests.py | Purpose: Turn persistently unfilled logistics requests into automatic mall work.
"""Request ages use the owned world's ticks, never wall time or policy weights."""
from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path

from training.laya_layout_stream import atomic_json

REQUEST_WAIT_TICKS = 20 * 60


def observe(root: Path, facts: Mapping) -> dict:
    """Persist continuous request ages; return overdue net production shortages.

    Local inventory and assigned deliveries are already removed by the survey.
    Allocate each network's transferable stock once, after construction reserves.
    A fulfilled/removed request disappears. A rewound world starts fresh ages.
    """
    path = root / "evidence" / "mall-requests.json"
    previous = json.loads(path.read_text()) if path.exists() else {}
    tick = int(facts.get("tick") or 0)
    old = previous.get("requests", {}) if tick >= previous.get("tick", 0) else {}
    pending = {}
    for request in facts.get("pending_requests") or ():
        if int(request["missing"]) <= 0:
            continue
        key = request["request_id"]
        pending[key] = {**request, "first_tick": old.get(key, {}).get("first_tick", tick)}
    stock = {network: {item: max(0, int(count) - int((entry.get("reserved") or {}).get(item, 0))
                                       - int((entry.get("logistic_pickups") or {}).get(item, 0)))
                       for item, count in (entry.get("items") or {}).items()}
             for network, entry in (facts.get("networks") or {}).items()}
    demand: dict[str, int] = defaultdict(int)
    rows = []
    for key, request in sorted(pending.items(), key=lambda pair: (pair[1]["first_tick"], pair[0])):
        item = request["item"]
        age = tick - request["first_tick"]
        supported = request.get("normal_eligible", True)
        available = stock.setdefault(request["network"], {})
        covered = min(request["missing"], available.get(item, 0)) if supported else 0
        if supported:
            available[item] = available.get(item, 0) - covered
        missing = int(request["missing"]) - covered
        overdue = age >= REQUEST_WAIT_TICKS
        if missing and overdue and supported:
            demand[item] += missing
        rows.append({**request, "age_ticks": age, "production_shortfall": missing,
                     "overdue": overdue,
                     "status": "unsupported_quality" if not supported else
                               "stock_available" if not missing else "queued" if overdue else "waiting"})
    state = {"tick": tick, "requests": pending}
    if state != previous:
        atomic_json(path, state)
    return {"demand": dict(demand), "priority": list(demand), "pending": rows}
