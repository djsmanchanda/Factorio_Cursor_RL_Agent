# Path: tests/test_logistic_inventory_lua.py
# Purpose: Pin the read-only real-base logistic inventory report contract.

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "factorio_mod" / "logistic_inventory.lua"
CONTROL = ROOT / "factorio_mod" / "control.lua"


def test_logistic_inventory_module_is_registered_from_control() -> None:
    source = MODULE.read_text(encoding="utf-8")
    control = CONTROL.read_text(encoding="utf-8")

    assert '"logistic_inventory"' in source
    assert 'network.get_contents()' in source
    assert 'require("logistic_inventory")' in control


def test_logistic_inventory_report_is_explicitly_scoped_and_read_only() -> None:
    source = MODULE.read_text(encoding="utf-8")

    assert 'game.surfaces[payload.surface]' in source
    assert 'game.forces[payload.force]' in source
    assert 'surface.find_entities_filtered({ type = "roboport", force = force })' in source
    assert 'REPORT_DIRECTORY .. "logistic_inventory_" .. game.tick .. ".json"' in source
    assert "roboport.insert(" not in source
    assert "roboport.remove(" not in source
    assert "network.insert(" not in source
    assert "network.remove(" not in source
