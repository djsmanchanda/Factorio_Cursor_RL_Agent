# Path: tests/test_laya_mall_view.py | Purpose: Protect bounded spectator focus and normal-speed viewing without policy actions.

from copy import deepcopy
import json
import pytest
from training.laya_mall_view import watch
from training.laya_saved_world import SavedWorldError
from training.laya_mall_supply import new_supply, accrue_supply, record_delivery


class World:
    surface='laya-mall/test';force='laya-mall-test'
    def __init__(self,root,players=None):
        self.root=root;self.scenario={'max_episode_ticks':180};self.tick=1000;self.steps=[];self.focuses=[];self.revision=0;self.ledger=new_supply(1000,{})
        self.pump_sizes=iter([30,90,60]);self.live=None
        self.players=players if players is not None else [{'name':'viewer','surface':'nauvis','force':'player','spectator':False}]
    def require_clear_mutations(self):pass
    def mark_viewing_only(self):self.marked=True
    def query(self):return {'tick':self.tick,'elapsed_ticks':self.tick-1000,'revision':self.revision,'ledger':deepcopy(self.ledger)}
    def rpc(self,op,**fields):
        if op=='view_status':return {'players':self.players}
        assert op=='view' and fields['observer']=='viewer'
        self.focuses.append(fields['observer'])
        self.players=[{'name':'viewer','surface':self.surface,'force':self.force,'spectator':True}]
        return {'observer':'viewer',**{k:self.players[0][k] for k in ('surface','force','spectator')}}
    def advance(self,*a,**k):pytest.fail('live viewer must never stop between individual clock quanta')
    def stream_rpc(self,op,**fields):
        assert fields['revision']==self.revision
        if op=='live_start':
            self.steps.append((fields['ticks'],1));self.endpoint=self.tick+fields['ticks']
            self.live={'id':fields['nonce'],'closed':False,'reason':'complete'};inserted={}
        else:
            assert fields['view_id']==self.live['id']
            if op=='live_pump':self.tick=min(self.endpoint,self.tick+next(self.pump_sizes))
            accrue_supply(self.ledger,self.tick)
            inserted={k:v//120 for k,v in self.ledger['accrued_120ths'].items()};record_delivery(self.ledger,inserted)
            self.live['closed']=self.tick==self.endpoint or op=='live_finish'
        self.revision+=1
        return {'view':deepcopy(self.live),'ledger':deepcopy(self.ledger),'inserted':inserted,'revision':self.revision,'tick':self.tick}


def test_view_focuses_one_named_spectator_and_uses_separate_bounded_window(tmp_path,monkeypatch):
    from training import laya_mall_view
    monkeypatch.setattr(laya_mall_view.time,"sleep",lambda _:None)
    world=World(tmp_path)
    world.scenario['max_episode_ticks']=60
    result=watch(world,seconds=3,observer='viewer')
    assert world.focuses==['viewer'] and world.steps==[(180,1)] and world.marked
    assert result['status']=='paused_view_complete' and result['advanced_ticks']==180
    assert not result['policy_running'] and not result['comparison_eligible']
    assert world.ledger['fixture_delivered']=={'iron-plate':3,'copper-plate':3}
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


def test_interruption_reconciles_and_closes_the_owned_stream(tmp_path,monkeypatch):
    from training import laya_mall_view
    world=World(tmp_path)
    def interrupt(_):raise KeyboardInterrupt()
    monkeypatch.setattr(laya_mall_view.time,'sleep',interrupt)
    with pytest.raises(KeyboardInterrupt):watch(world,observer='viewer')
    assert world.live['closed']
    assert json.loads((tmp_path/'live-view.json').read_text())['status']=='stopped_interrupted'


def test_spectator_copy_cannot_execute_or_advance_a_training_step(tmp_path):
    from training.laya_mall_world import MallWorld
    adapter=object.__new__(MallWorld);adapter.manifest={'viewing_only':True}
    with pytest.raises(SavedWorldError,match='spectator copy'):adapter.advance(60)
    with pytest.raises(SavedWorldError,match='spectator copy'):adapter.execute('anything',[])
