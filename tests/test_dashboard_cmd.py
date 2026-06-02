"""
tests/test_dashboard_cmd.py — Phase 13.

The `guardops dashboard` launcher: it should hand the FastAPI import string to
uvicorn with the resolved host/port, and fail with a friendly install hint when the
optional 'dashboard' extra is missing.
"""

import importlib.util
from unittest.mock import patch

from cli.commands.dashboard_cmd import dashboard_command

_REAL_FIND_SPEC = importlib.util.find_spec


def _config():
    return {
        "project": {"name": "demo"},
        "dashboard": {"host": "127.0.0.1", "port": 8081},
        "metadata": {"backend": "sqlite"},
    }


def test_dashboard_launches_uvicorn(runner):
    with patch("cli.commands.dashboard_cmd.load_config", return_value=_config()), \
         patch("uvicorn.run") as mock_run:
        result = runner.invoke(dashboard_command, ["--port", "9000"])
    assert result.exit_code == 0, result.output
    mock_run.assert_called_once()
    args, kwargs = mock_run.call_args
    assert args[0] == "backend.dashboard.app:app"
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 9000


def test_dashboard_missing_extra_is_friendly(runner):
    def fake_find_spec(name, *a, **k):
        if name in ("uvicorn", "fastapi"):
            return None
        return _REAL_FIND_SPEC(name, *a, **k)

    with patch("cli.commands.dashboard_cmd.importlib.util.find_spec", side_effect=fake_find_spec):
        result = runner.invoke(dashboard_command, [])
    assert result.exit_code == 1
    assert "guardops[dashboard]" in result.output
