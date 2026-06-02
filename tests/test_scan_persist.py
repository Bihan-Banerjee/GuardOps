"""
tests/test_scan_persist.py — Phase 12.

Proves the scan→metadata wiring:
  - `guardops scan` calls persist_report_safe with the built report
  - a metadata DB write failure NEVER breaks the scan (exit code stays 0)

All scanners are skipped so the test needs no external security tools; the report
is therefore clean and the command exits 0 on the happy path.
"""

from unittest.mock import patch


from cli.commands.scan_cmd import scan_command

_SKIP_ALL = ["--skip-semgrep", "--skip-bandit", "--skip-trivy", "--skip-sonarqube", "--no-report"]
_CONFIG = {"project": {"name": "demo"}}


def test_scan_invokes_persist(runner, tmp_path):
    config = {**_CONFIG, "metadata": {"path": str(tmp_path / "g.db")}}
    with patch("cli.commands.scan_cmd.load_config", return_value=config), \
         patch("cli.commands.scan_cmd.persist_report_safe") as mock_persist:
        result = runner.invoke(scan_command, _SKIP_ALL)
    assert result.exit_code == 0
    assert mock_persist.called
    # report is the first positional arg; config the second
    args, kwargs = mock_persist.call_args
    assert args[1] is config
    assert kwargs.get("source") == "scan"


def test_scan_persists_run_to_db(runner, tmp_path):
    db = str(tmp_path / "g.db")
    config = {**_CONFIG, "metadata": {"backend": "sqlite", "path": db}}
    with patch("cli.commands.scan_cmd.load_config", return_value=config):
        result = runner.invoke(scan_command, _SKIP_ALL)
    assert result.exit_code == 0
    # A clean run was recorded.
    from backend.metadata.sqlite_store import SqliteMetadataStore
    runs = SqliteMetadataStore(db).list_runs()
    assert len(runs) == 1
    assert runs[0].project_name == "demo"
    assert runs[0].source == "scan"


def test_scan_db_failure_is_non_fatal(runner, tmp_path):
    # Point the DB at a path whose parent is a regular file → mkdir/connect fails.
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    bad_db = str(blocker / "nested" / "g.db")
    config = {**_CONFIG, "metadata": {"backend": "sqlite", "path": bad_db}}
    with patch("cli.commands.scan_cmd.load_config", return_value=config):
        result = runner.invoke(scan_command, _SKIP_ALL)
    # The scan must still pass despite the DB write failing.
    assert result.exit_code == 0
