"""
tests/test_verify_cmd.py — `guardops verify-image`.

Exits 0 only when the cosign signature (and optional attestation) verifies; cosign
missing or a bad signature exits 1. cosign is mocked.
"""

from types import SimpleNamespace
from unittest.mock import patch

from cli.commands.verify_cmd import verify_image_command

REF = "123.dkr.ecr.ap-south-1.amazonaws.com/guardops-app@sha256:abc"


def _res(success=True, skipped=False, skip_reason="", error_message=""):
    return SimpleNamespace(success=success, skipped=skipped,
                           skip_reason=skip_reason, error_message=error_message)


def test_cosign_missing_exits_one(runner):
    with patch("cli.commands.verify_cmd.verify_image",
               return_value=_res(success=False, skipped=True, skip_reason="cosign not installed")):
        result = runner.invoke(verify_image_command, [REF])
    assert result.exit_code == 1
    assert "cosign not installed" in result.output


def test_bad_signature_exits_one(runner):
    with patch("cli.commands.verify_cmd.verify_image",
               return_value=_res(success=False, error_message="bad sig")):
        result = runner.invoke(verify_image_command, [REF])
    assert result.exit_code == 1
    assert "FAILED" in result.output


def test_signature_ok(runner):
    with patch("cli.commands.verify_cmd.verify_image", return_value=_res(success=True)):
        result = runner.invoke(verify_image_command, [REF])
    assert result.exit_code == 0
    assert "Signature verified" in result.output


def test_attestation_ok(runner):
    with patch("cli.commands.verify_cmd.verify_image", return_value=_res(success=True)), \
         patch("cli.commands.verify_cmd.verify_attestation", return_value=_res(success=True)):
        result = runner.invoke(verify_image_command, [REF, "--attestation"])
    assert result.exit_code == 0
    assert "attestation verified" in result.output.lower()


def test_attestation_failure_exits_one(runner):
    with patch("cli.commands.verify_cmd.verify_image", return_value=_res(success=True)), \
         patch("cli.commands.verify_cmd.verify_attestation",
               return_value=_res(success=False, error_message="no att")):
        result = runner.invoke(verify_image_command, [REF, "--attestation"])
    assert result.exit_code == 1
    assert "Attestation verification FAILED" in result.output
