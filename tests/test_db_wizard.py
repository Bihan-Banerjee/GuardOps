"""
tests/test_db_wizard.py — db command branches + deploy-wizard helpers.
"""

from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner

from cli.commands.db_cmd import db_group
from cli.commands import _deploy_wizard as dw

_R = CliRunner()


def _raise(*a, **k):
    raise Exception("boom")


def _store(**kw):
    return SimpleNamespace(**kw)


# ── db_cmd ────────────────────────────────────────────────────────────────────

def test_db_init_failure():
    store = _store(init_schema=_raise)
    with patch("cli.commands.db_cmd.open_store", return_value=({}, store)), \
         patch("cli.commands.db_cmd.resolve_metadata_db_path", return_value="x.db"):
        r = _R.invoke(db_group, ["init"])
    assert r.exit_code == 1 and "Could not initialize" in r.output


def test_db_prune_no_retention():
    with patch("cli.commands.db_cmd.open_store", return_value=({"metadata": {}}, _store())):
        r = _R.invoke(db_group, ["prune"])
    assert r.exit_code == 0 and "No retention" in r.output


def test_db_prune_failure():
    store = _store(prune=lambda **k: SimpleNamespace(success=False, error_message="x"))
    with patch("cli.commands.db_cmd.open_store", return_value=({}, store)):
        r = _R.invoke(db_group, ["prune", "--keep-last", "5"])
    assert r.exit_code == 1 and "Prune failed" in r.output


def test_db_prune_nothing_and_success():
    store0 = _store(prune=lambda **k: SimpleNamespace(success=True, runs_deleted=0, findings_deleted=0))
    with patch("cli.commands.db_cmd.open_store", return_value=({}, store0)):
        r0 = _R.invoke(db_group, ["prune", "--keep-last", "5"])
    assert "Nothing to prune" in r0.output

    store1 = _store(prune=lambda **k: SimpleNamespace(success=True, runs_deleted=3, findings_deleted=9))
    with patch("cli.commands.db_cmd.open_store", return_value=({}, store1)):
        r1 = _R.invoke(db_group, ["prune", "--keep-days", "30"])
    assert "Pruned" in r1.output


def test_db_export_failure():
    store = _store(export_json=_raise)
    with patch("cli.commands.db_cmd.open_store", return_value=({}, store)):
        r = _R.invoke(db_group, ["export"])
    assert r.exit_code == 1 and "Could not export" in r.output


def test_db_export_to_s3_failure():
    store = _store(export_json=lambda: {"runs": []})
    cfg = {"metadata": {"s3_bucket": "b"}, "project": {"name": "p"}}
    with patch("cli.commands.db_cmd.open_store", return_value=(cfg, store)), \
         patch("backend.metadata.s3_store.put_export_to_s3", side_effect=Exception("s3 down")):
        r = _R.invoke(db_group, ["export", "--to-s3"])
    assert r.exit_code == 1 and "S3 upload failed" in r.output


# ── _deploy_wizard helpers ────────────────────────────────────────────────────

def test_prompt_wrappers():
    with patch("cli.commands._deploy_wizard.Prompt.ask", return_value="x"):
        assert dw._ask("q") == "x"
    with patch("cli.commands._deploy_wizard.Confirm.ask", return_value=True):
        assert dw._confirm("q") is True
    with patch("cli.commands._deploy_wizard.IntPrompt.ask", return_value=3):
        assert dw._ask_int("q", default=1) == 3


def test_should_offer_wizard_interactive_and_yes():
    assert dw.should_offer_wizard(None, True, False) is True
    assert dw.should_offer_wizard(None, False, True) is False


def test_should_offer_wizard_ci(monkeypatch):
    monkeypatch.setenv("CI", "true")
    assert dw.should_offer_wizard(None, False, False) is False


def test_should_offer_wizard_non_tty(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    assert dw.should_offer_wizard(None, False, False) is False
