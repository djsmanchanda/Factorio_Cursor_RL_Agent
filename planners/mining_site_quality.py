# Path: planners/mining_site_quality.py | Purpose: Prefer resource-rich interior sites using observed survey samples.
from __future__ import annotations


def site_quality(centres, samples, spacing=4):
    """Weakest drill first: prefer surrounding ore, then remaining resource.

    Coarse survey cells rank proposals only. Exact mining-area validation is
    still required before placement; sparse small patches remain candidates.
    """
    scores = []
    for x, y in centres:
        near = [s for s in samples if max(abs(s["position"]["x"] - x),
                                           abs(s["position"]["y"] - y)) <= spacing * 1.5]
        scores.append((len(near), sum(float(s.get("remaining", 0)) for s in near)))
    return min(scores, default=(0, 0))
