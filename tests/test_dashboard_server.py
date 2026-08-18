# Path: tests/test_dashboard_server.py
# Purpose: Protect the dashboard's local RCON-secret loading contract.

from pathlib import Path

import pytest

from tools.dashboard_server import _rcon_password


def test_rcon_secret_file_overrides_command_line_password(tmp_path: Path) -> None:
    secret = tmp_path / "rcon-password"
    secret.write_text("local-secret\n", encoding="utf-8")

    assert _rcon_password(secret, "fallback-password") == "local-secret"


def test_rcon_secret_file_fails_closed_when_empty_or_unreadable(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.write_text("\n", encoding="utf-8")

    with pytest.raises(ValueError, match="empty"):
        _rcon_password(empty, "fallback-password")
    with pytest.raises(ValueError, match="unavailable"):
        _rcon_password(tmp_path / "missing", "fallback-password")
