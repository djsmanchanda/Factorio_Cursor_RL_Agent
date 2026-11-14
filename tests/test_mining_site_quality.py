# Path: tests/test_mining_site_quality.py | Purpose: Prefer interior support and ore lifetime without banning small patches.
from planners.mining_site_quality import site_quality


def test_interior_coverage_precedes_edge_density_then_remaining_amount():
    samples = [{'position': {'x': x, 'y': y}, 'remaining': 100}
               for x in range(0, 21, 4) for y in range(0, 21, 4)]
    assert site_quality([(8, 8)], samples) > site_quality([(0, 0)], samples)
    richer = [{**s, 'remaining': 1000} for s in samples]
    assert site_quality([(8, 8)], richer) > site_quality([(8, 8)], samples)
    assert site_quality([(0, 0)], samples[:1])[0] == 1
