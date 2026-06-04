"""
tests/test_cosign_verifier.py — backend/security/cosign_verifier.

Covers the identity regexp builder and the verify_image / verify_attestation
skip / success / failure / timeout paths. cosign is mocked.
"""

import subprocess
from unittest.mock import MagicMock

from backend.security.cosign_verifier import (
    default_identity_regexp,
    verify_image,
    verify_attestation,
)

REF = "123.dkr.ecr.ap-south-1.amazonaws.com/guardops-app@sha256:abc"


def _have_cosign(monkeypatch, yes=True):
    monkeypatch.setattr("backend.security.cosign_verifier.shutil.which",
                        lambda _: "/usr/bin/cosign" if yes else None)


def _run(monkeypatch, returncode, stdout="", stderr=""):
    monkeypatch.setattr("backend.security.cosign_verifier.subprocess.run",
                        lambda *a, **k: MagicMock(returncode=returncode, stdout=stdout, stderr=stderr))


# ── pure ──────────────────────────────────────────────────────────────────────

def test_identity_regexp_escapes_dots():
    rx = default_identity_regexp("owner/Repo.Name")
    assert r"Repo\.Name" in rx
    assert r"ci\.yaml" in rx


# ── verify_image ──────────────────────────────────────────────────────────────

def test_cosign_missing_is_skipped(monkeypatch):
    _have_cosign(monkeypatch, yes=False)
    res = verify_image(REF)
    assert res.skipped and not res.success


def test_verify_image_success(monkeypatch):
    _have_cosign(monkeypatch)
    _run(monkeypatch, 0, stdout='{"ok":true}')
    res = verify_image(REF)
    assert res.success and res.verified and res.kind == "signature"


def test_verify_image_failure(monkeypatch):
    _have_cosign(monkeypatch)
    _run(monkeypatch, 1, stderr="no matching signatures")
    res = verify_image(REF)
    assert not res.success and "no matching signatures" in res.error_message


def test_verify_image_timeout(monkeypatch):
    _have_cosign(monkeypatch)

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="cosign", timeout=1)

    monkeypatch.setattr("backend.security.cosign_verifier.subprocess.run", boom)
    res = verify_image(REF)
    assert not res.success and "timed out" in res.error_message


# ── verify_attestation ────────────────────────────────────────────────────────

def test_verify_attestation_success(monkeypatch):
    _have_cosign(monkeypatch)
    _run(monkeypatch, 0, stdout="verified")
    res = verify_attestation(REF, attestation_type="cyclonedx")
    assert res.success and res.kind == "attestation:cyclonedx"
