# Path: tests/test_laya_host_scheduler.py | Purpose: Verify the host slot gate judges UPS relative to each world's game speed.
import json

import pytest

from tools import laya_host_scheduler as host

pytestmark = pytest.mark.fast


def world(tmp_path, name, speed_field, ups):
    root = tmp_path/name
    (root/'logs').mkdir(parents=True)
    lines = [f'RUN START: ts=2026-11-05T10:00:00+05:30 command=run runner=laya-clean mode=live {speed_field} policy=guided']
    lines += [f'+{t}s DECISION {i} phase=C4 tick={int(t*ups)} need=wait' for i, t in enumerate(range(0, 181, 30))]
    (root/'logs/laya-runner.log').write_text('\n'.join(lines)+'\n')
    (root/'laya-runner.heartbeat.json').write_text(json.dumps({'elapsed_s': 180}))
    return root


def test_floors_compare_one_x_equivalent_ups_and_show_measured(tmp_path):
    worlds = [world(tmp_path, 'fast', 'speed=2.0', 120), world(tmp_path, 'slow', 'speed=2.0', 100),
              world(tmp_path, 'legacy', 'decisions=unlimited', 60)]
    metrics = host.measure([host.Cohort('c', tmp_path, 0, 3, 0, 0, worlds)], window_s=180)
    assert metrics['game_speeds'] == [1.0, 2.0]
    assert metrics['measured_ups_mean'] == pytest.approx((120+100+60)/3)
    # A 2x world at 100 UPS is at 50 of 60 target: below a 52 floor, as a 1x world at 50 would be.
    assert metrics['ups_p98'] == pytest.approx(50, abs=.5)
    assert metrics['total_ups'] == pytest.approx(60+50+60)
