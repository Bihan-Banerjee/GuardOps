"""
tests/test_dashboard_cmd_extra.py — `guardops dashboard snapshot` subcommand.

Stdout / --out / --to-s3 / missing-bucket. load_settings + build_snapshot +
put_export_to_s3 are mocked.
"""

from unittest.mock import patch

from cli.commands.dashboard_cmd import dashboard_command


def _snap():
    return {"generated_at": "t", "version": "1.0.0", "data": {"/api/v1/runs": {"runs": []}}}


def _patches(config):
    return (
        patch("cli.commands.dashboard_cmd.load_config", return_value=config),
        patch("backend.dashboard.settings.load_settings", return_value=object()),
        patch("backend.dashboard.snapshot.build_snapshot", return_value=_snap()),
    )


def test_snapshot_to_stdout(runner):
    a, b, c = _patches({})
    with a, b, c:
        r = runner.invoke(dashboard_command, ["snapshot"])
    assert r.exit_code == 0 and "generated_at" in r.output


def test_snapshot_to_file(runner, tmp_path):
    out = tmp_path / "snap.json"
    a, b, c = _patches({})
    with a, b, c:
        r = runner.invoke(dashboard_command, ["snapshot", "--out", str(out)])
    assert r.exit_code == 0 and out.exists()


def test_snapshot_to_s3(runner):
    a, b, c = _patches({"metadata": {"s3_bucket": "mybucket"}})
    with a, b, c, \
         patch("backend.metadata.s3_store.put_export_to_s3",
               return_value="s3://mybucket/dashboard/snapshot.json"):
        r = runner.invoke(dashboard_command, ["snapshot", "--to-s3"])
    assert r.exit_code == 0 and "Published" in r.output


def test_snapshot_to_s3_no_bucket_exits(runner):
    a, b, c = _patches({})
    with a, b, c:
        r = runner.invoke(dashboard_command, ["snapshot", "--to-s3"])
    assert r.exit_code == 1 and "No S3 bucket" in r.output
