# Path: tests/test_factory_invariants.py
# Purpose: Verify tools/verify_factory_invariants.py against a fake RCON client (no live game).

from __future__ import annotations

import json

import pytest

from tools import verify_factory_invariants as vfi


class FakeRconClient:
    """Routes /sc queries to canned responses by substring match, in rule order."""

    def __init__(self, rules: list[tuple[str, str]]):
        self._rules = rules
        self.commands: list[str] = []

    def command(self, text: str) -> str:
        self.commands.append(text)
        for needle, response in self._rules:
            if needle in text:
                return response
        raise AssertionError(f"FakeRconClient: no rule matched command: {text!r}")

    def close(self) -> None:
        pass


def healthy_rules(**overrides: str) -> list[tuple[str, str]]:
    """Canned responses describing a single-network, ghost-free, fluid-flowing world."""
    rules = {
        "energy": "1",
        "electric": json.dumps(
            {
                "sampled": 5,
                "networks": [
                    {
                        "id": 7,
                        "examples": [
                            {"name": "assembling-machine-1", "position": {"x": 1.0, "y": 1.0}}
                        ],
                    }
                ],
            }
        ),
        "logistic": json.dumps(
            {"total_roboports": 2, "without_network": 0, "networks": [{"id": 3, "count": 2}]}
        ),
        "ghosts": json.dumps({"total": 0, "sample": []}),
        "fluid": json.dumps(
            [
                {
                    "name": "oil-refinery",
                    "position": {"x": 2.0, "y": 2.0},
                    "status": "working",
                    "boxes": [
                        {
                            "index": 1,
                            "production_type": "input",
                            "fluid_name": "crude-oil",
                            "amount": 100,
                        }
                    ],
                }
            ]
        ),
        "counts": json.dumps({"assembling-machine-1": 1, "oil-refinery": 1}),
    }
    rules.update(overrides)
    return [
        ("electric-energy-interface", rules["energy"]),
        ("e.electric_network_id", rules["electric"]),
        ("without_network", rules["logistic"]),
        ("ghost_revive", rules["ghosts"]),
        ("defines.entity_status", rules["fluid"]),
        ("find_entities_filtered{}", rules["counts"]),
    ]


# ---------------------------------------------------------------------------
# Criterion 1: energy_interface_count
# ---------------------------------------------------------------------------

def test_energy_interface_count_pass():
    client = FakeRconClient([("electric-energy-interface", "1")])
    result = vfi.measure_energy_interface_count(client, "planner-sandbox")
    assert result == {"status": vfi.PASS, "count": 1, "expected": 1}


def test_energy_interface_count_fail_when_zero():
    client = FakeRconClient([("electric-energy-interface", "0")])
    result = vfi.measure_energy_interface_count(client, "planner-sandbox")
    assert result["status"] == vfi.FAIL
    assert result["count"] == 0


def test_energy_interface_count_unknown_when_surface_missing():
    client = FakeRconClient([("electric-energy-interface", "ERROR:no-surface")])
    result = vfi.measure_energy_interface_count(client, "nope")
    assert result["status"] == vfi.UNKNOWN


# ---------------------------------------------------------------------------
# Criterion 2: electric_networks
# ---------------------------------------------------------------------------

def test_electric_networks_pass_single_network():
    client = FakeRconClient(
        [
            (
                "e.electric_network_id",
                json.dumps(
                    {
                        "sampled": 3,
                        "networks": [
                            {"id": 5, "examples": [{"name": "inserter", "position": {"x": 0, "y": 0}}]}
                        ],
                    }
                ),
            )
        ]
    )
    result = vfi.measure_electric_networks(client, "planner-sandbox")
    assert result["status"] == vfi.PASS
    assert result["network_ids"] == [5]


def test_electric_networks_fail_when_two_distinct_ids():
    client = FakeRconClient(
        [
            (
                "e.electric_network_id",
                json.dumps(
                    {
                        "sampled": 4,
                        "networks": [
                            {"id": 5, "examples": []},
                            {"id": 9, "examples": []},
                        ],
                    }
                ),
            )
        ]
    )
    result = vfi.measure_electric_networks(client, "planner-sandbox")
    assert result["status"] == vfi.FAIL
    assert result["network_ids"] == [5, 9]


def test_electric_networks_unknown_when_nothing_sampled():
    client = FakeRconClient(
        [("e.electric_network_id", json.dumps({"sampled": 0, "networks": {}}))]
    )
    result = vfi.measure_electric_networks(client, "planner-sandbox")
    assert result["status"] == vfi.UNKNOWN


def test_electric_networks_unknown_on_garbled_response():
    # measure_electric_networks raises on unparseable input; run_all (tested below)
    # is the boundary that turns this into an "unknown" criterion rather than a crash.
    client = FakeRconClient([("e.electric_network_id", "")])
    with pytest.raises(vfi.MeasurementError):
        vfi.measure_electric_networks(client, "planner-sandbox")


def test_electric_networks_empty_lua_table_parses_as_no_networks():
    # helpers.table_to_json serializes an empty Lua array as '{}', not '[]'.
    client = FakeRconClient(
        [("e.electric_network_id", json.dumps({"sampled": 2, "networks": {}}))]
    )
    result = vfi.measure_electric_networks(client, "planner-sandbox")
    assert result["network_ids"] == []
    assert result["status"] == vfi.FAIL  # zero distinct ids != exactly one


# ---------------------------------------------------------------------------
# Criterion 3: logistic_networks
# ---------------------------------------------------------------------------

def test_logistic_networks_pass():
    client = FakeRconClient(
        [
            (
                "without_network",
                json.dumps(
                    {"total_roboports": 3, "without_network": 0, "networks": [{"id": 1, "count": 3}]}
                ),
            )
        ]
    )
    result = vfi.measure_logistic_networks(client, "planner-sandbox")
    assert result["status"] == vfi.PASS
    assert result["network_ids"] == [1]


def test_logistic_networks_fail_when_some_roboports_uncovered():
    client = FakeRconClient(
        [
            (
                "without_network",
                json.dumps(
                    {"total_roboports": 3, "without_network": 1, "networks": [{"id": 1, "count": 2}]}
                ),
            )
        ]
    )
    result = vfi.measure_logistic_networks(client, "planner-sandbox")
    assert result["status"] == vfi.FAIL


def test_logistic_networks_unknown_when_no_roboports():
    client = FakeRconClient(
        [("without_network", json.dumps({"total_roboports": 0, "without_network": 0, "networks": {}}))]
    )
    result = vfi.measure_logistic_networks(client, "planner-sandbox")
    assert result["status"] == vfi.UNKNOWN


# ---------------------------------------------------------------------------
# Criterion 4: pending_ghosts
# ---------------------------------------------------------------------------

def test_pending_ghosts_pass_when_zero():
    client = FakeRconClient([("ghost_revive", json.dumps({"total": 0, "sample": []}))])
    result = vfi.measure_pending_ghosts(client, "planner-sandbox")
    assert result["status"] == vfi.PASS
    assert result["count"] == 0


def test_pending_ghosts_fail_with_reasons():
    canned = json.dumps(
        {
            "total": 1,
            "sample": [
                {
                    "ghost_name": "assembling-machine-1",
                    "position": {"x": 4.0, "y": 4.0},
                    "can_place": False,
                    "in_logistic_network": True,
                    "item_name": "assembling-machine-1",
                    "item_count": 0,
                    "in_construction_range": True,
                }
            ],
        }
    )
    client = FakeRconClient([("ghost_revive", canned)])
    result = vfi.measure_pending_ghosts(client, "planner-sandbox")
    assert result["status"] == vfi.FAIL
    assert result["count"] == 1
    reasons = result["sample"][0]["reasons"]
    assert "tile blocked" in reasons
    assert any("unavailable" in r for r in reasons)


def test_pending_ghosts_diagnoses_missing_item_and_out_of_range():
    canned = json.dumps(
        {
            "total": 1,
            "sample": [
                {
                    "ghost_name": "assembling-machine-1",
                    "position": {"x": 4.0, "y": 4.0},
                    "can_place": True,
                    "in_logistic_network": False,
                    "item_name": "assembling-machine-1",
                    "item_count": None,
                    "in_construction_range": False,
                }
            ],
        }
    )
    client = FakeRconClient([("ghost_revive", canned)])
    result = vfi.measure_pending_ghosts(client, "planner-sandbox")
    reasons = result["sample"][0]["reasons"]
    assert "outside any roboport construction range" in reasons
    assert any("not covered by any logistic network" in r for r in reasons)


# ---------------------------------------------------------------------------
# Criterion 5: fluid_machines
# ---------------------------------------------------------------------------

def test_fluid_machines_pass():
    canned = json.dumps(
        [
            {
                "name": "oil-refinery",
                "position": {"x": 1.0, "y": 1.0},
                "status": "working",
                "boxes": [
                    {"index": 1, "production_type": "input", "fluid_name": "crude-oil", "amount": 50}
                ],
            }
        ]
    )
    client = FakeRconClient([("defines.entity_status", canned)])
    result = vfi.measure_fluid_machines(client, "planner-sandbox")
    assert result["status"] == vfi.PASS
    assert result["violations"] == []


def test_fluid_machines_fail_when_input_box_empty():
    canned = json.dumps(
        [
            {
                "name": "oil-refinery",
                "position": {"x": 1.0, "y": 1.0},
                "status": "working",
                "boxes": [
                    {"index": 1, "production_type": "input", "fluid_name": "crude-oil", "amount": 0}
                ],
            }
        ]
    )
    client = FakeRconClient([("defines.entity_status", canned)])
    result = vfi.measure_fluid_machines(client, "planner-sandbox")
    assert result["status"] == vfi.FAIL
    assert len(result["violations"]) == 1


def test_fluid_machines_fail_when_refinery_not_working():
    canned = json.dumps(
        [
            {
                "name": "oil-refinery",
                "position": {"x": 1.0, "y": 1.0},
                "status": "no_power",
                "boxes": [
                    {"index": 1, "production_type": "input", "fluid_name": "crude-oil", "amount": 50}
                ],
            }
        ]
    )
    client = FakeRconClient([("defines.entity_status", canned)])
    result = vfi.measure_fluid_machines(client, "planner-sandbox")
    assert result["status"] == vfi.FAIL


def test_fluid_machines_unknown_when_none_found():
    client = FakeRconClient([("defines.entity_status", "{}")])
    result = vfi.measure_fluid_machines(client, "planner-sandbox")
    assert result["status"] == vfi.UNKNOWN


# ---------------------------------------------------------------------------
# Criterion 6: idempotency baseline/compare
# ---------------------------------------------------------------------------

def test_diff_entity_counts_detects_added_entity():
    baseline = {"inserter": 2, "assembling-machine-1": 1}
    current = {"inserter": 2, "assembling-machine-1": 2}
    diff = vfi.diff_entity_counts(baseline, current)
    assert diff == {"added": {"assembling-machine-1": 1}, "removed": {}}


def test_diff_entity_counts_detects_removed_entity():
    baseline = {"inserter": 2}
    current = {"inserter": 1}
    diff = vfi.diff_entity_counts(baseline, current)
    assert diff == {"added": {}, "removed": {"inserter": 1}}


def test_diff_entity_counts_no_change():
    baseline = {"inserter": 2}
    current = {"inserter": 2}
    diff = vfi.diff_entity_counts(baseline, current)
    assert diff == {"added": {}, "removed": {}}


def test_measure_idempotency_writes_baseline(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    client = FakeRconClient([("find_entities_filtered{}", json.dumps({"inserter": 2}))])
    result = vfi.measure_idempotency(client, "planner-sandbox", str(baseline_path), None)
    assert result["status"] == vfi.UNKNOWN  # a single run cannot prove idempotency
    assert json.loads(baseline_path.read_text(encoding="utf-8")) == {"inserter": 2}


def test_measure_idempotency_compare_pass_when_unchanged(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps({"inserter": 2}), encoding="utf-8")
    client = FakeRconClient([("find_entities_filtered{}", json.dumps({"inserter": 2}))])
    result = vfi.measure_idempotency(client, "planner-sandbox", None, str(baseline_path))
    assert result["status"] == vfi.PASS
    assert result["diff"] == {"added": {}, "removed": {}}


def test_measure_idempotency_compare_fail_when_entity_added(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps({"inserter": 2}), encoding="utf-8")
    client = FakeRconClient([("find_entities_filtered{}", json.dumps({"inserter": 3}))])
    result = vfi.measure_idempotency(client, "planner-sandbox", None, str(baseline_path))
    assert result["status"] == vfi.FAIL
    assert result["diff"]["added"] == {"inserter": 1}


def test_measure_idempotency_unknown_without_baseline_or_compare():
    client = FakeRconClient([("find_entities_filtered{}", json.dumps({"inserter": 2}))])
    result = vfi.measure_idempotency(client, "planner-sandbox", None, None)
    assert result["status"] == vfi.UNKNOWN


def test_measure_idempotency_unknown_when_compare_file_missing(tmp_path):
    client = FakeRconClient([("find_entities_filtered{}", json.dumps({"inserter": 2}))])
    result = vfi.measure_idempotency(
        client, "planner-sandbox", None, str(tmp_path / "does-not-exist.json")
    )
    assert result["status"] == vfi.UNKNOWN


def test_measure_idempotency_unknown_when_compare_file_garbled(tmp_path):
    bad_path = tmp_path / "garbled.json"
    bad_path.write_text("not json{{{", encoding="utf-8")
    client = FakeRconClient([("find_entities_filtered{}", json.dumps({"inserter": 2}))])
    result = vfi.measure_idempotency(client, "planner-sandbox", None, str(bad_path))
    assert result["status"] == vfi.UNKNOWN


# ---------------------------------------------------------------------------
# Overall verdict / run_all
# ---------------------------------------------------------------------------

def test_run_all_verdict_unknown_on_healthy_world_without_baseline():
    # Idempotency (criterion 6) inherently needs two runs; a single healthy run
    # cannot prove it, so the overall verdict is "unknown", never "pass".
    client = FakeRconClient(healthy_rules())
    report = vfi.run_all(client, "planner-sandbox")
    assert report["criteria_status"]["idempotency"] == vfi.UNKNOWN
    assert report["verdict"] == vfi.UNKNOWN
    for name in vfi.CRITERIA:
        if name != "idempotency":
            assert report["criteria_status"][name] == vfi.PASS


def test_run_all_verdict_pass_on_healthy_world_with_matching_baseline(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps({"assembling-machine-1": 1, "oil-refinery": 1}), encoding="utf-8"
    )
    client = FakeRconClient(healthy_rules())
    report = vfi.run_all(client, "planner-sandbox", compare_path=str(baseline_path))
    assert report["verdict"] == vfi.PASS


def test_run_all_verdict_fail_when_two_electric_networks():
    two_network_electric = json.dumps(
        {
            "sampled": 4,
            "networks": [
                {"id": 1, "examples": [{"name": "inserter", "position": {"x": 0, "y": 0}}]},
                {"id": 2, "examples": [{"name": "roboport", "position": {"x": 10, "y": 10}}]},
            ],
        }
    )
    client = FakeRconClient(healthy_rules(electric=two_network_electric))
    report = vfi.run_all(client, "planner-sandbox")
    assert report["verdict"] == vfi.FAIL
    assert report["criteria_status"]["electric_networks"] == vfi.FAIL


def test_run_all_verdict_unknown_when_a_query_returns_garbled_response():
    client = FakeRconClient(healthy_rules(fluid="not-json-at-all"))
    report = vfi.run_all(client, "planner-sandbox")
    assert report["criteria_status"]["fluid_machines"] == vfi.UNKNOWN
    assert report["verdict"] == vfi.UNKNOWN


def test_run_all_verdict_unknown_when_a_query_returns_empty_response():
    client = FakeRconClient(healthy_rules(energy=""))
    report = vfi.run_all(client, "planner-sandbox")
    assert report["criteria_status"]["energy_interface_count"] == vfi.UNKNOWN
    assert report["verdict"] == vfi.UNKNOWN


def test_format_report_includes_all_six_criteria():
    client = FakeRconClient(healthy_rules())
    report = vfi.run_all(client, "planner-sandbox")
    text = vfi.format_report(report)
    for marker in ("1. energy_interface_count", "2. electric_networks", "3. logistic_networks",
                   "4. pending_ghosts", "5. fluid_machines", "6. idempotency"):
        assert marker in text


# ---------------------------------------------------------------------------
# Exit code mapping via main()
# ---------------------------------------------------------------------------

def _patch_client(monkeypatch, rules):
    def fake_constructor(host, port, password, timeout=10.0):
        return FakeRconClient(rules)

    monkeypatch.setattr(vfi, "RconClient", fake_constructor)


def test_main_exit_code_zero_on_pass(monkeypatch, capsys, tmp_path):
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps({"assembling-machine-1": 1, "oil-refinery": 1}), encoding="utf-8"
    )
    _patch_client(monkeypatch, healthy_rules())
    code = vfi.main(["--rcon-password", "x", "--compare", str(baseline_path)])
    assert code == 0


def test_main_exit_code_three_on_unknown_single_run_no_baseline(monkeypatch, capsys):
    _patch_client(monkeypatch, healthy_rules())
    code = vfi.main(["--rcon-password", "x"])
    assert code == 3  # idempotency cannot be proven in a single run


def test_main_exit_code_two_on_fail(monkeypatch, capsys):
    two_network_electric = json.dumps(
        {
            "sampled": 4,
            "networks": [
                {"id": 1, "examples": []},
                {"id": 2, "examples": []},
            ],
        }
    )
    _patch_client(monkeypatch, healthy_rules(electric=two_network_electric))
    code = vfi.main(["--rcon-password", "x"])
    assert code == 2


def test_main_exit_code_three_on_unknown(monkeypatch, capsys):
    _patch_client(monkeypatch, healthy_rules(fluid="garbled"))
    code = vfi.main(["--rcon-password", "x"])
    assert code == 3


def test_main_exit_code_three_when_rcon_connection_fails(monkeypatch, capsys):
    def broken_constructor(host, port, password, timeout=10.0):
        raise vfi.RconError("connection refused")

    monkeypatch.setattr(vfi, "RconClient", broken_constructor)
    code = vfi.main(["--rcon-password", "x"])
    assert code == 3


def test_main_json_output_is_valid_json(monkeypatch, capsys, tmp_path):
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps({"assembling-machine-1": 1, "oil-refinery": 1}), encoding="utf-8"
    )
    _patch_client(monkeypatch, healthy_rules())
    vfi.main(["--rcon-password", "x", "--json", "--compare", str(baseline_path)])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["verdict"] == vfi.PASS


def test_main_baseline_and_compare_round_trip(monkeypatch, capsys, tmp_path):
    baseline_path = tmp_path / "baseline.json"
    _patch_client(monkeypatch, healthy_rules())
    code = vfi.main(["--rcon-password", "x", "--baseline", str(baseline_path)])
    assert code == 3  # idempotency unknown on a single run
    assert baseline_path.exists()

    _patch_client(monkeypatch, healthy_rules())
    code = vfi.main(["--rcon-password", "x", "--compare", str(baseline_path)])
    assert code == 0  # unchanged counts -> idempotent -> overall pass
