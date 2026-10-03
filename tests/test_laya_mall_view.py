# Path: tests/test_laya_mall_view.py | Purpose: Protect bounded spectator focus and normal-speed viewing without policy actions.

import json
import pytest
from training.laya_mall_view import watch
from training.laya_saved_world import SavedWorldError


class World:
    surface='laya-mall/test';force='laya-mall-test'
    def __init__(self,root,players=None):
        self.root=root;self.scenario={'max_episode_ticks':180};self.tick=1000;self.steps=[];self.focuses=[]
        self.players=players if players is not None else [{'name':'viewer','surface':'nauvis','force':'player','spectator':False}]
    def require_clear_mutations(self):pass
    def query(self):return {'tick':self.tick,'elapsed_ticks':self.tick-1000}
    def rpc(self,op,**fields):
        if op=='view_status':return {'players':self.players}
        assert op=='view' and fields['observer']=='viewer'
        self.focuses.append(fields['observer'])
        self.players=[{'name':'viewer','surface':self.surface,'force':self.force,'spectator':True}]
        return {'observer':'viewer',**{k:self.players[0][k] for k in ('surface','force','spectator')}}
    def advance(self,ticks,*,speed):self.steps.append((ticks,speed));self.tick+=ticks


def test_view_focuses_one_named_spectator_and_clamps_to_existing_episode_budget(tmp_path):
    world=World(tmp_path)
    result=watch(world,seconds=10,observer='viewer')
    assert world.focuses==['viewer'] and world.steps==[(60,1)]*3
    assert result['status']=='paused_episode_budget' and result['advanced_ticks']==180
    assert not result['policy_running'] and not result['comparison_eligible']
    assert json.loads((tmp_path/'live-view.json').read_text())==result


def test_view_automatic_selection_refuses_ambiguous_players_without_advancing(tmp_path):
    world=World(tmp_path,players=[{'name':'viewer'},{'name':'someone-else'}])
    with pytest.raises(SavedWorldError,match='multiple connected'):watch(world)
    assert not world.steps and not world.focuses


def test_view_waits_for_observer_without_using_engine_ticks(tmp_path,monkeypatch):
    from training import laya_mall_view
    world=World(tmp_path,players={})
    times=iter([0,2]);monkeypatch.setattr(laya_mall_view.time,'monotonic',lambda:next(times))
    assert watch(world,connect_timeout=1)['status']=='paused_observer_timeout'
    assert not world.steps and not world.focuses


def test_view_rejects_unbounded_duration_and_invalid_speed_before_engine_access(tmp_path):
    from training.laya_mall_world import MallWorld
    world=World(tmp_path)
    with pytest.raises(SavedWorldError,match='duration'):watch(world,seconds=601)
    adapter=object.__new__(MallWorld)
    with pytest.raises(SavedWorldError,match='speed'):adapter.advance(60,speed=0)
    with pytest.raises(SavedWorldError,match='speed'):adapter.advance(60,speed=True)
