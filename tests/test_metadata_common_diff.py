"""
tests/test_metadata_common_diff.py — _metadata_common helpers + diff command branches.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cli.commands._metadata_common import severity_rank, fmt_ts, short, severity_cell, fail
from cli.commands.diff_cmd import diff_command


# ── _metadata_common ──────────────────────────────────────────────────────────

def test_metadata_common_helpers():
    assert severity_rank("HIGH") >= 0
    assert severity_rank("NONSENSE") == -1          # ValueError branch
    assert fmt_ts("") == "—"
    assert fmt_ts("2026-06-04T10:00:00Z") == "2026-06-04 10:00:00"
    assert short(None, 10) == ""
    assert short("a" * 20, 10).endswith("…")
    assert severity_cell("HIGH").startswith("[")
    assert severity_cell("WEIRD") == "WEIRD"


def test_fail_exits():
    with pytest.raises(SystemExit):
        fail("boom")


# ── diff command ──────────────────────────────────────────────────────────────

def _run(store, args=None):
    with patch("cli.commands.diff_cmd.open_store", return_value=({}, store)):
        return _RUNNER.invoke(diff_command, args or [])


from click.testing import CliRunner  # noqa: E402
_RUNNER = CliRunner()


def test_diff_no_runs():
    store = SimpleNamespace(list_runs=lambda project=None, limit=1: [])
    r = _run(store)
    assert r.exit_code == 0 and "No scan runs" in r.output


def test_diff_to_id_not_found():
    store = SimpleNamespace(get_run=lambda rid: None)
    r = _run(store, ["--to", "99"])
    assert r.exit_code == 1 and "not found" in r.output


def test_diff_from_id_not_found():
    target = SimpleNamespace(id=2, project_name="p")
    store = SimpleNamespace(get_run=lambda rid: target if rid == 2 else None)
    r = _run(store, ["--to", "2", "--from", "99"])
    assert r.exit_code == 1 and "not found" in r.output


def test_diff_exception_is_reported():
    def boom(*a, **k):
        raise RuntimeError("db corrupt")
    store = SimpleNamespace(list_runs=boom)
    r = _run(store)
    assert r.exit_code == 1 and "Could not compute diff" in r.output


def test_diff_json_regressed_exits_one():
    to_run = SimpleNamespace(id=2, project_name="p", to_dict=lambda: {"id": 2})
    from_run = SimpleNamespace(id=1, project_name="p", to_dict=lambda: {"id": 1})
    new_crit = SimpleNamespace(fingerprint="fp-new", severity="CRITICAL", to_dict=lambda: {"sev": "CRITICAL"})
    store = SimpleNamespace(
        list_runs=lambda project=None, limit=1: [to_run],
        latest_run_before=lambda run_id=None, project=None: from_run,
        query_findings=lambda run_id=None, limit=0: ([new_crit] if run_id == 2 else []),
    )
    r = _run(store, ["--json-output"])
    assert r.exit_code == 1


def test_diff_json_no_regression_returns_zero():
    to_run = SimpleNamespace(id=2, project_name="p", to_dict=lambda: {"id": 2})
    from_run = SimpleNamespace(id=1, project_name="p", to_dict=lambda: {"id": 1})
    new_low = SimpleNamespace(fingerprint="fp-low", severity="LOW", to_dict=lambda: {"sev": "LOW"})
    store = SimpleNamespace(
        list_runs=lambda project=None, limit=1: [to_run],
        latest_run_before=lambda run_id=None, project=None: from_run,
        query_findings=lambda run_id=None, limit=0: ([new_low] if run_id == 2 else []),
    )
    r = _run(store, ["--json-output"])
    assert r.exit_code == 0
