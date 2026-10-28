# Path: tests/test_laya_cohort_dashboard.py | Purpose: Exercise admission safety and isolated dashboard evidence contracts.
import concurrent.futures
import http.client
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from tools import laya_cohort_dashboard as dashboard
from tools import laya_host_scheduler as scheduler


def metrics(**changes):
    result = {"active_slots": 10, "queued": 10, "ups_samples": 10, "ups_p95": 120, "ups_p98": 115,
              "total_ups": 1200, "temp_c": 70, "available_gb": 20, "power_profile": "balanced", "unstable": []}
    result.update(changes)
    return result


def test_ceiling_is_not_a_target_and_ups_still_drains():
    config = {**scheduler.DEFAULTS, "manual_target": 20, "unhealthy_samples": 1}
    state = {"target": 10, "mode": "balanced", "settle_until": 9999}
    target, reason, _ = scheduler.choose(config, state, metrics(ups_p95=80, ups_p98=80), now=100)
    assert (target, reason) == (8, "unhealthy")
    target, reason, _ = scheduler.choose(config, {"target": 10}, metrics(ups_p95=None, ups_p98=None), now=100)
    assert (target, reason) == (10, "no_measurement")


def test_zero_ceiling_recovery_bootstraps_only_safe_queued_work():
    config = {**scheduler.DEFAULTS, "manual_target": 0}
    empty = metrics(active_slots=0, queued=2, ups_samples=0, ups_p95=None, ups_p98=None)
    assert scheduler.choose(config, {"target": 0}, empty, now=100)[0] == 0
    config["manual_target"] = 4
    assert scheduler.choose(config, {"target": 0}, empty, now=100)[:2] == (1, "bootstrap_unmeasured")
    assert scheduler.choose(config, {"target": 0}, {**empty, "temp_c": 100}, now=100)[0] == 0
    assert scheduler.choose(config, {"target": 0}, {**empty, "queued": 0}, now=100)[0] == 0


def test_thermal_and_zero_ceiling_do_not_kill_worlds(tmp_path):
    config = {**scheduler.DEFAULTS, "manual_target": 20}
    assert scheduler.choose(config, {"target": 10}, metrics(temp_c=100), now=100)[:2] == (8, "thermal_hard")
    config["manual_target"] = 0
    assert scheduler.choose(config, {"target": 10}, metrics(), now=100)[:2] == (0, "manual_ceiling")
    cohort = scheduler.Cohort("example", tmp_path, 0, 10, 5, 0)
    assert scheduler.allocate(0, [cohort]) == {"example": 10}
    config.update(manual_target=3, unhealthy_samples=1)
    assert scheduler.choose(config, {"target": 3}, metrics(active_slots=3, ups_p95=80), now=100)[:2] == (1, "unhealthy")


def test_temporary_ceilings_restore_prior_policy(tmp_path):
    scheduler.main(["--root", str(tmp_path), "target", "12"])
    scheduler.main(["--root", str(tmp_path), "target", "8", "--for", "1h"])
    scheduler.main(["--root", str(tmp_path), "target", "3", "--for", "1s"])
    config = scheduler._load_config(tmp_path)
    restored, _ = scheduler._expire_manual(config, tmp_path, config["manual_target_until"] + 1, True)
    assert restored["manual_target"] == 8
    restored, _ = scheduler._expire_manual(restored, tmp_path, restored["manual_target_until"] + 1, True)
    assert restored["manual_target"] == 12
    scheduler.main(["--root", str(tmp_path), "target", "7"])
    assert scheduler._load_config(tmp_path)["manual_stack"] == []


def test_concurrent_config_writers_preserve_changes(tmp_path):
    script = Path(scheduler.__file__)
    def call(args):
        return subprocess.run([sys.executable, str(script), "--root", str(tmp_path), *args], capture_output=True, text=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(call, [["configure", "--min-ups-p95", "115"], ["mode", "cool"]]))
    assert all(result.returncode == 0 for result in results)
    config = scheduler._load_config(tmp_path)
    assert config["mode"] == "cool" and config["min_ups_p95"] == 115


@pytest.mark.parametrize("args", [["--min-ups-p95", "nan"], ["--min-ups-p95", "80", "--min-ups-p98", "90"], ["--temp-limit", "120"]])
def test_invalid_guard_config_rejected(tmp_path, args):
    with pytest.raises(SystemExit):
        scheduler.main(["--root", str(tmp_path), "configure", *args])


def test_cohort_accounting_and_notes(tmp_path):
    registry = tmp_path / "registry"
    cohort = registry / "cohort"
    cohort.mkdir(parents=True)
    (cohort / "freeze.json").write_text(json.dumps({"planned_jobs": 4, "arms": [{"id": "a", "revision": "abc"}]}))
    (cohort / "job-status.json").write_text(json.dumps({"jobs": [{"id": "r", "arm": "a", "status": "completed", "accepted_ever": ["C6"], "accepted_final": []}]}))
    app = dashboard.Dashboard(tmp_path, registry, tmp_path / "host", tmp_path / "data")
    row = app.cohorts()[0]
    assert row["planned"] == 4 and row["unaccounted"] == 3 and row["counts"] == {"completed": 1}
    assert row["jobs"][0]["accepted_ever"] == ["C6"] and row["jobs"][0]["accepted_final"] == []
    app.note({"cohort": "cohort", "run": "r", "text": "<script>alert(1)</script>", "tags": "observation"})
    assert dashboard.tail_jsonl(tmp_path / "data/notes.jsonl")[0]["kind"] == "user_observation"


def test_bounded_tail(tmp_path):
    path = tmp_path / "large.jsonl"
    path.write_text('{"id": 1}\n' + 'x' * 200 + '\n{"id": 2}\n')
    assert dashboard.tail_jsonl(path, 30) == [{"id": 2}]


def test_http_host_origin_and_token_guards(tmp_path):
    app = dashboard.Dashboard(tmp_path, tmp_path / "registry", tmp_path / "host", tmp_path / "data")
    server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
    server.dashboard = app
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_port
        conn = http.client.HTTPConnection("127.0.0.1", port)
        payload = json.dumps({"cohort": "x", "run": "y", "text": "note", "tags": ""})
        headers = {"Host": f"127.0.0.1:{port}", "Origin": "http://evil.example", "X-Laya-Token": app.token, "Content-Type": "application/json"}
        conn.request("POST", "/api/note", payload, headers)
        assert conn.getresponse().status == 403
        headers["Origin"] = f"http://127.0.0.1:{port}"
        headers["X-Laya-Token"] = "wrong"
        conn.request("POST", "/api/note", payload, headers)
        assert conn.getresponse().status == 403
        headers["X-Laya-Token"] = app.token
        conn.request("POST", "/api/note", payload, headers)
        assert conn.getresponse().status == 200
        conn.request("GET", "/api/snapshot", headers={"Host": "evil.example"})
        assert conn.getresponse().status == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_cooling_renewal_stale_timer_and_explicit_mode(tmp_path, monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr(dashboard.subprocess, "run", run)
    now = [1000.0]
    monkeypatch.setattr(dashboard.time, "time", lambda: now[0])
    app = dashboard.Dashboard(tmp_path, tmp_path / "registry", tmp_path / "host", tmp_path / "data", True)
    monkeypatch.setattr(app, "_check_recovery_timer", lambda: None)
    scheduler._set_config(app.host, mode="balanced")
    app.cool(True)
    old = dashboard.read_json(app.data / "cooling.json")["token"]
    now[0] += 100
    app.cool(True)
    current = dashboard.read_json(app.data / "cooling.json")
    assert current["token"] != old and current["previous_mode"] == "balanced"
    now[0] += 3601
    app.restore(old)
    assert dashboard.read_json(app.data / "cooling.json")["token"] == current["token"]
    scheduler.main(["--root", str(app.host), "mode", "performance"])
    app2 = dashboard.Dashboard(tmp_path, app.registry, app.host, app.data, True)
    app2.restore(current["token"])
    assert scheduler._load_config(app.host)["mode"] == "performance"
    assert calls[-1] == ["powerprofilesctl", "set", "balanced"]
    assert dashboard.read_json(app.data / "cooling.json") == {}


def test_cooling_apply_failure_rolls_back_and_audits(tmp_path, monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1 if command[-1] == "power-saver" else 0, "", "failed")
    monkeypatch.setattr(dashboard.subprocess, "run", run)
    app = dashboard.Dashboard(tmp_path, tmp_path / "registry", tmp_path / "host", tmp_path / "data", True)
    monkeypatch.setattr(app, "_check_recovery_timer", lambda: None)
    scheduler._set_config(app.host, mode="balanced")
    with pytest.raises(ValueError, match="recovery"):
        app.cool(True)
    assert calls[-1][-1] == "balanced"
    assert scheduler._load_config(app.host)["mode"] == "balanced"
    assert dashboard.read_json(app.data / "cooling.json") == {}
    assert any(row["action"] == "cool_apply_failed" for row in dashboard.tail_jsonl(app.data / "audit.jsonl"))


def test_recovery_retries_failed_restore_after_restart(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(dashboard.time, "time", lambda: now[0])
    app = dashboard.Dashboard(tmp_path, tmp_path / "registry", tmp_path / "host", tmp_path / "data", True)
    monkeypatch.setattr(app, "_check_recovery_timer", lambda: None)
    failure = [False]
    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, int(failure[0]), "", "failed")
    monkeypatch.setattr(dashboard.subprocess, "run", run)
    app.cool(True)
    now[0] += 3601
    failure[0] = True
    with pytest.raises(ValueError):
        app.recover()
    assert dashboard.read_json(app.data / "cooling.json")["phase"] == "active"
    failure[0] = False
    dashboard.Dashboard(tmp_path, app.registry, app.host, app.data).recover()
    assert dashboard.read_json(app.data / "cooling.json") == {}


def test_controls_fail_closed_without_compatible_scheduler(tmp_path):
    app = dashboard.Dashboard(tmp_path, tmp_path / "registry", tmp_path / "host", tmp_path / "data", True)
    assert not app.snapshot()["controls_enabled"]
    with pytest.raises(ValueError, match="heartbeat|identity|protocol"):
        app.control({"action": "resume"})


def test_running_scheduler_handshake_and_control(tmp_path):
    host, registry = tmp_path / "host", tmp_path / "registry"
    scheduler._set_config(host, publish_gate=False)
    process = subprocess.Popen([sys.executable, str(Path(scheduler.__file__).resolve()), "--root", str(host),
                                "--registry", str(registry), "run", "--interval", "60"], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    try:
        assert json.loads(process.stdout.readline())["target"] >= 0
        app = dashboard.Dashboard(tmp_path, registry, host, tmp_path / "data", True)
        assert app.scheduler_compatibility()[0]
        app.control({"action": "ceiling", "slots": 0, "duration": ""})
        assert scheduler._load_config(host)["manual_target"] == 0
    finally:
        process.terminate()
        process.communicate(timeout=5)


def test_cooling_failure_retains_pending_recovery(tmp_path, monkeypatch):
    app = dashboard.Dashboard(tmp_path, tmp_path / "registry", tmp_path / "host", tmp_path / "data", True)
    monkeypatch.setattr(app, "_check_recovery_timer", lambda: None)
    monkeypatch.setattr(dashboard.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", "failed"))
    with pytest.raises(ValueError, match="recovery"):
        app.cool(True)
    assert dashboard.read_json(app.data / "cooling.json")["phase"] == "applying"
    assert scheduler._load_config(app.host)["mode"] == "cool"
    actions = [row["action"] for row in dashboard.tail_jsonl(app.data / "audit.jsonl")]
    assert "cool_apply_failed" in actions and "cool_rollback_pending" in actions


def test_listener_requires_world_process_and_owned_socket(tmp_path):
    world = tmp_path / "world"
    (world / "server").mkdir(parents=True)
    (world / "server/factorio.pid").write_text("123")
    proc = tmp_path / "proc/123"
    (proc / "fd").mkdir(parents=True)
    (proc / "net").mkdir()
    (proc / "exe").symlink_to(tmp_path / "factorio")
    (proc / "cmdline").write_bytes(b"factorio\0--config\0" + str(world).encode() + b"/server/config.ini\0--rcon-password\0secret\0")
    (proc / "fd/4").symlink_to("socket:[42]")
    (proc / "net/udp").write_text("header\n  0: 0100007F:A042 00000000:0000 07 0:0 0:0 0 0 0 42\n")
    row = dashboard.listener_evidence(world, 41026, tmp_path / "proc")
    assert row["bind"] == "127.0.0.1" and row["endpoint"] is None
    assert "loopback" in row["status"] and "secret" not in json.dumps(row)
    (proc / "fd/4").unlink()
    assert "not found" in dashboard.listener_evidence(world, 41026, tmp_path / "proc")["status"]


def test_run_timings_and_notes_pagination_preserve_old_observations(tmp_path, monkeypatch):
    registry = tmp_path / 'registry'
    cohort = registry / 'cohort'
    world = cohort / 'world'
    (world / 'evidence').mkdir(parents=True)
    (cohort / 'job-status.json').write_text(json.dumps({'jobs': [{'id': 'r', 'world': str(world)}]}))
    rows = [{'index': 1, 'recorded_at': '2026-10-28T12:00:00+05:30', 'outcome': {}},
            {'index': 2, 'recorded_at': '2026-10-28T12:00:07+05:30', 'outcome': {'timing': {
                'policy_wall_seconds': .25, 'operation_wall_seconds': 2, 'model_client_seconds': None}}}]
    (world / 'evidence/decision-states.jsonl').write_text('\n'.join(map(json.dumps, rows)))
    (world / 'manifest.json').write_text('{}')
    app = dashboard.Dashboard(tmp_path, registry, tmp_path / 'host', tmp_path / 'data')
    for i in range(12):
        app.note({'cohort': 'cohort', 'run': 'r', 'text': f'Note {i} ' + 'x'*200, 'tags': ''})
    monkeypatch.setattr(dashboard, 'MAX_TAIL', 900)
    found, cursor = [], None
    while True:
        page = app.notes_page('cohort', 'r', cursor)
        found += [row['text'].split()[1] for row in page['rows']]
        cursor = page['next_cursor']
        if cursor is None:
            break
    assert sorted(map(int, found)) == list(range(12))
    details = app.run_details('cohort', 'r')
    assert details['timing'][1]['policy_seconds'] == .25
    assert details['timing'][1]['executor_seconds'] == 2
    assert details['timing'][1]['decision_interval_seconds'] == 7
    assert details['timing'][0]['model_client_seconds'] is None
    with pytest.raises(ValueError):
        app.run_details('../outside', 'r')


def test_viewer_udp_round_trip_and_identity_expiry():
    import socket
    import time
    from tools.laya_cohort_view import ViewerRelay
    target = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    target.bind(('127.0.0.1', 0))
    target.settimeout(2)
    viewer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    viewer.settimeout(2)
    valid = [True]
    relay = ViewerRelay('127.0.0.1', '127.0.0.1', target.getsockname(), lambda: valid[0])
    address, port = relay.endpoint.split(':')
    try:
        viewer.sendto(b'join probe', (address, int(port)))
        packet, source = target.recvfrom(100)
        assert packet == b'join probe'
        target.sendto(b'answer', source)
        assert viewer.recvfrom(100)[0] == b'answer'
        relay.deadline = time.time() - 1
        relay.thread.join(timeout=2)
        assert relay.stopped.is_set()
    finally:
        relay.close()
        target.close()
        viewer.close()


def test_history_ranges_incremental_reads_partial_lines_and_extrema(tmp_path):
    from tools.laya_cohort_history import History
    source = tmp_path/'history.jsonl'
    now = 1_000_000
    samples = [{'updated': now-6*86400+i*60, 'reason': 'hold', 'metrics': {
        'ups_p95': 50 if i == 3 else 110, 'temp_c': 99 if i == 4 else 75}} for i in range(8600)]
    source.write_text(''.join(json.dumps(r)+'\n' for r in samples))
    history = History(source, tmp_path/'history.sqlite3')
    week = history.window(168, now=now)
    day = history.window(24, now=now)
    six = history.window(6, now=now)
    assert week['raw_samples'] > day['raw_samples'] > six['raw_samples']
    assert len(week['rows']) <= 1500
    assert min(r['metrics']['ups_p95'] for r in week['rows']) == 50
    assert max(r['metrics']['temp_c'] for r in week['rows']) == 99
    last = json.dumps({'updated': now, 'reason': 'thermal', 'metrics': {'temp_c': 90}})
    with source.open('a') as file: file.write(last)
    assert history.window(6, now=now)['rows'][-1]['updated'] != now
    with source.open('a') as file: file.write('\n')
    assert history.window(6, now=now)['rows'][-1]['updated'] == now
    assert history.window(6, now=now)['raw_samples'] == history.window(6, now=now)['raw_samples']


def test_mod_sync_verifies_bytes_and_archives_replaced_client_mods(tmp_path):
    import hashlib
    from tools.laya_cohort_mod_sync import make_archive, apply_archive
    source, target = tmp_path/'server', tmp_path/'gui'
    (source/'my_mod').mkdir(parents=True)
    (source/'my_mod/control.lua').write_text('new')
    (source/'mod-list.json').write_text('{"mods":[{"name":"my_mod","enabled":true}]}')
    (target/'my_mod').mkdir(parents=True)
    (target/'my_mod/old.lua').write_text('old')
    (target/'other_mod.zip').write_text('preserve')
    expected = {'my_mod/control.lua': hashlib.sha256(b'new').hexdigest()}
    archive = tmp_path/'mods.tar.gz'
    make_archive(source, expected, archive)
    receipt = apply_archive(archive, target, tmp_path/'backup')
    assert (target/'my_mod/control.lua').read_text() == 'new'
    assert not (target/'my_mod/old.lua').exists()
    assert (tmp_path/'backup/previous/my_mod/old.lua').read_text() == 'old'
    assert (target/'other_mod.zip').read_text() == 'preserve'
    assert receipt['restart_client_required'] is True
    (source/'my_mod/control.lua').write_text('changed after freeze')
    with pytest.raises(ValueError, match='differs'):
        make_archive(source, expected, tmp_path/'bad.tar.gz')


def test_mod_sync_rejects_archive_path_escape_before_client_changes(tmp_path):
    import io
    import tarfile
    from tools.laya_cohort_mod_sync import apply_archive
    archive = tmp_path/'bad.tar'
    with tarfile.open(archive, 'w') as tar:
        item = tarfile.TarInfo('mods/../../escaped')
        item.size = 1
        tar.addfile(item, io.BytesIO(b'x'))
    with pytest.raises(ValueError, match='Unsafe'):
        apply_archive(archive, tmp_path/'gui', tmp_path/'backup')
    assert not (tmp_path/'escaped').exists()


def test_curated_sources_allow_reports_without_exposing_unlisted_data(tmp_path):
    from urllib.parse import urlencode
    (tmp_path/'docs/rl').mkdir(parents=True)
    (tmp_path/'data/cohort').mkdir(parents=True)
    (tmp_path/'data/cohort/report.md').write_text('reviewed report')
    (tmp_path/'data/cohort/freeze.json').write_text('{"safe":true}')
    (tmp_path/'data/private.json').write_text('{"private":true}')
    (tmp_path/'docs/rl/laya_cohort_curated_history.json').write_text(json.dumps({'cohorts':[{
        'sources':['data/cohort/report.md','data/cohort/freeze.json']}]}))
    app=dashboard.Dashboard(tmp_path,tmp_path/'registry',tmp_path/'host',tmp_path/'state')
    server=dashboard.ThreadingHTTPServer(('127.0.0.1',0),dashboard.Handler)
    server.dashboard=app
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port)
        for path,expected in [('data/cohort/report.md',200),('data/cohort/freeze.json',200),
                              ('data/private.json',404),('../outside.md',404)]:
            connection.request('GET','/api/report?'+urlencode({'path':path}))
            response=connection.getresponse()
            assert response.status==expected
            response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_dashboard_assets_stay_csp_clean_and_mutations_stay_explicit():
    import re
    import shutil
    html = (dashboard.ASSETS / "index.html").read_text()
    script = (dashboard.ASSETS / "app.js").read_text()
    assert not re.search(r"<script(?![^>]*\bsrc=)", html) and not re.search(r"\son[a-z]+=", html)
    assert 'src="/app.js"' in html and 'href="/style.css"' in html
    # The only mutating endpoints, each posted from a named action, never from render/refresh code.
    assert set(re.findall(r"post\('(/api/[a-z-]+)'", script)) == {"/api/control", "/api/note", "/api/sync-mods", "/api/view"}
    assert script.count("post('/api/sync-mods'") == 1 and "function startSync" in script and "sync-form" in script
    assert not re.search(r"(localStorage|sessionStorage|save\([^)]*)[^\n]*\btoken\b", script.replace("token = next.token", ""))
    if shutil.which("node"):
        assert subprocess.run(["node", "--check", str(dashboard.ASSETS / "app.js")], capture_output=True).returncode == 0
