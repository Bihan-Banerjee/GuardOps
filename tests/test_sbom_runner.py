"""
tests/test_sbom_runner.py — backend/security/sbom_runner.run_syft.

Covers the skip (syft missing), timeout, non-zero exit, success-but-no-files, and
success-with-package-count paths. syft is mocked; the "written" SBOM is faked on disk.
"""

import subprocess
from unittest.mock import MagicMock

from backend.security.sbom_runner import run_syft


def _have_syft(monkeypatch, yes=True):
    monkeypatch.setattr("backend.security.sbom_runner.shutil.which",
                        lambda _: "/usr/bin/syft" if yes else None)


def test_syft_not_installed_is_skipped(monkeypatch):
    _have_syft(monkeypatch, yes=False)
    res = run_syft("img:latest")
    assert res.skipped and not res.success
    assert "not installed" in res.skip_reason


def test_syft_timeout(monkeypatch, tmp_path):
    _have_syft(monkeypatch)

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="syft", timeout=1)

    monkeypatch.setattr("backend.security.sbom_runner.subprocess.run", boom)
    res = run_syft("img:latest", output_dir=str(tmp_path))
    assert not res.success and "timed out" in res.error_message


def test_syft_nonzero_exit(monkeypatch, tmp_path):
    _have_syft(monkeypatch)
    monkeypatch.setattr("backend.security.sbom_runner.subprocess.run",
                        lambda *a, **k: MagicMock(returncode=1, stderr="boom", stdout=""))
    res = run_syft("img:latest", output_dir=str(tmp_path))
    assert not res.success and "exit 1" in res.error_message


def test_syft_success_but_no_files(monkeypatch, tmp_path):
    _have_syft(monkeypatch)
    monkeypatch.setattr("backend.security.sbom_runner.subprocess.run",
                        lambda *a, **k: MagicMock(returncode=0, stderr="", stdout=""))
    res = run_syft("img:latest", output_dir=str(tmp_path))   # nothing written
    assert not res.success and "no SBOM files" in res.error_message


def test_syft_success_counts_packages(monkeypatch, tmp_path):
    _have_syft(monkeypatch)
    monkeypatch.setattr("backend.security.sbom_runner.subprocess.run",
                        lambda *a, **k: MagicMock(returncode=0, stderr="", stdout=""))
    # simulate what syft would write
    (tmp_path / "sbom.cdx.json").write_text('{"components":[{"name":"a"},{"name":"b"}]}',
                                            encoding="utf-8")
    res = run_syft("img:latest", output_dir=str(tmp_path))
    assert res.success
    assert res.package_count == 2
    assert "cyclonedx-json" in res.formats
