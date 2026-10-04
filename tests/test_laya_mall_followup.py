# Path: tests/test_laya_mall_followup.py | Purpose: Keep mall-slot screens matched by checkpoint, time window and common patience without default adoption.
from collections import defaultdict
from pathlib import Path
from tools import run_laya_mall_followup as followup


def test_followup_pairs_share_patience_checkpoint_and_window(monkeypatch):
    monkeypatch.setattr(followup,'load_bundle',lambda p:{'verified':True})
    variants,jobs=followup.matrix([Path('/test/earned-a'),Path('/test/earned-b'),Path('/test/earned-c')])
    groups=defaultdict(list)
    raw=[j for j in jobs if j['kind']=='raw']
    components=[j for j in jobs if j['kind']=='component']
    for j in raw:groups[(j['case'],j['seed'])].append(j)
    assert len(jobs)>300 and len(variants)>20
    assert variants['baseline']['settings']=={'stall_decisions':48}
    assert all(v['settings']['stall_decisions']==48 for v in variants.values())
    for rows in groups.values():
        assert sum(r['variant']=='baseline' for r in rows)==1
        assert len({r['resume_bundle'] for r in rows})==1
        assert len({r['max_ticks'] for r in rows})==1
    assert {j['window_game_minutes'] for j in raw}=={120,180}
    assert all(j['max_ticks']==j['window_game_minutes']*3600 for j in raw)
    assert len(components)==54 and {j['component']['family'] for j in components}=={'circuit','plastic'}
