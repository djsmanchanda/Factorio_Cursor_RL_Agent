# Path: tests/test_laya_reference_interface.py | Purpose: Verify the common interface retains the proven policy source.
import hashlib
import subprocess

import pytest

from tools.laya_reference_interface import install, REFERENCE

pytestmark = pytest.mark.fast


def test_interface_ports_onto_reference_without_policy_or_mod_changes(tmp_path):
    repo = __import__('pathlib').Path(__file__).resolve().parents[1]
    clone = tmp_path/'reference'
    subprocess.run(['git','clone','--quiet','--shared','--no-checkout',str(repo),str(clone)],check=True)
    subprocess.run(['git','checkout','--quiet',REFERENCE],cwd=clone,check=True)
    paths = ['training/laya_guided_policy.py','training/laya_variants.py','orchestrator/autonomous_builder.py',
             'orchestrator/checkpoint_milestones.py','training/laya_saved_world.lua']
    before = {p:hashlib.sha256((clone/p).read_bytes()).hexdigest() for p in paths}
    receipt = install(repo,clone)
    assert receipt['reference'] == REFERENCE
    assert receipt['policy_change'] is False
    assert before == {p:hashlib.sha256((clone/p).read_bytes()).hexdigest() for p in paths}
    help_text = subprocess.run([str(repo/'.venv/bin/python'),str(clone/'tools/run_laya_mixed.py'),'--help'],
                               cwd=clone,check=True,capture_output=True,text=True).stdout
    assert '--research-target' in help_text
    # The proven predicate schema has census rows absent from the older root
    # schema: exercising a real exported reference snapshot catches this drift.
    check = '''
import json
from pathlib import Path
from orchestrator.checkpoint_milestones import FactorySnapshot, CheckpointEvaluator
from training.laya_restart_state import export_restart_state, import_restart_state
sample = FactorySnapshot.from_mapping({'tick':123,'foundations':{'iron':{
    'machines':[{'position':{'x':1.5,'y':-2.5},'products_finished':None,
                 'status':'working','unit_number':42}], 'machine_count':1}}})
source=Path('data/serialization-source/server/script-output');source.mkdir(parents=True)
target=Path('data/serialization-target/server/script-output');target.mkdir(parents=True)
bundle=export_restart_state(source,'reference-origin','mods',sample,[sample],CheckpointEvaluator())
state=import_restart_state(json.loads(json.dumps(bundle)),target,episode_id='reference-origin',mod_scope='mods')
assert state.baseline == sample and state.history == (sample,)
'''
    subprocess.run([str(repo/'.venv/bin/python'),'-c',check],cwd=clone,check=True,capture_output=True)
