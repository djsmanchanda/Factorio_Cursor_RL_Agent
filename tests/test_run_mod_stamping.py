# Path: tests/test_run_mod_stamping.py | Purpose: Verify identical GUI artifact reuse and distinct Laya stamping without touching a real profile.
import json
from tools import run_mod_stamping as stamping

NAMES = ('factorio_cursor_rl_agent', 'factorio_laya_agent', 'factorio_training_lab')


def mod(root, name, version, code):
    root.mkdir(parents=True, exist_ok=True)
    (root/'info.json').write_text(json.dumps({'name':name,'version':version,'factorio_version':'2.0'}, indent=2)+'\n')
    (root/'control.lua').write_text(code)


def setup(tmp_path):
    server, gui = tmp_path/'server-mods', tmp_path/'gui-mods'
    gui.mkdir()
    (gui/'mod-list.json').write_text(json.dumps({'mods':[{'name':name,'enabled':True,'version':'0.2.0'} for name in NAMES]}))
    for name in NAMES:
        mod(server/name, name, '0.2.0', '-- current code\n')
        mod(gui/name, name, '0.2.0', '-- prior code\n')
    return server, gui


def test_new_laya_artifact_is_stamped_alongside_both_existing_mods(tmp_path):
    server, gui = setup(tmp_path)
    result = stamping.stamp(server, gui, 12001)
    assert set(result) == set(NAMES)
    assert json.loads((server/'factorio_laya_agent/info.json').read_text())['version'] == '0.2.12001'
    assert (gui/'factorio_laya_agent_0.2.12001/control.lua').is_file()


def test_unchanged_lua_reuses_exact_stamped_gui_artifact_across_python_revisions(tmp_path):
    server, gui = setup(tmp_path)
    stamping.stamp(server, gui, 12001)
    before = {p.name for p in gui.iterdir()}
    for name in NAMES:
        mod(server/name, name, '0.2.0', '-- current code\n')
    result = stamping.stamp(server, gui, 12002)
    assert {p.name for p in gui.iterdir()} == before
    for name in NAMES:
        assert result[name].startswith('0.2.12001 ')
        assert stamping.content_hash(server/name) == stamping.content_hash(gui/f'{name}_0.2.12001')


def test_changed_lua_keeps_prior_gui_defaults_and_installs_new_version(tmp_path):
    server, gui = setup(tmp_path)
    before = (gui/'mod-list.json').read_bytes()
    stamping.stamp(server, gui, 12001)
    (server/'factorio_laya_agent/control.lua').write_text('-- changed Laya\n')
    stamping.stamp(server, gui, 12002)
    assert (gui/'factorio_laya_agent_0.2.12001/control.lua').read_text() == '-- current code\n'
    assert (gui/'factorio_laya_agent_0.2.12002/control.lua').read_text() == '-- changed Laya\n'
    assert (gui/'mod-list.json').read_bytes() == before
