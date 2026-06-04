"""
tests/test_admission_cmd.py — v1.0.0.

`guardops admission` renders the Kyverno policy placeholders for the chosen mode and
applies them. Audit is the default (safe); Enforce blocks. Both modes are tested,
plus --dry-run (never shells out) and the missing-policy-dir guard.
"""

from unittest.mock import patch

import pytest

from cli.commands.admission_cmd import admission_command, render_policy

_POLICY = """\
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: verify-images
spec:
  validationFailureAction: __POLICY_ACTION__
  rules:
    - name: verify
      verifyImages:
        - mutateDigest: __DIGEST_PIN__
          attestors:
            - issuer: __CI_ISSUER__
              subject: __CI_SUBJECT__
"""


# ── pure rendering ────────────────────────────────────────────────────────────

def test_render_audit_is_safe():
    out = render_policy(_POLICY, "audit")
    assert "validationFailureAction: Audit" in out
    assert "mutateDigest: false" in out          # Kyverno forbids mutation in Audit
    assert "__POLICY_ACTION__" not in out


def test_render_enforce_blocks_and_pins_digest():
    out = render_policy(_POLICY, "enforce")
    assert "validationFailureAction: Enforce" in out
    assert "mutateDigest: true" in out


def test_render_substitutes_ci_identity():
    # The cosign keyless verification placeholders must be resolved, or the policy
    # ships broken (literal __CI_SUBJECT__).
    out = render_policy(_POLICY, "audit", github_repo="acme/widgets")
    assert "issuer: https://token.actions.githubusercontent.com" in out
    assert "acme/widgets/.github/workflows/ci.yaml@refs/heads/*" in out
    assert "__CI_" not in out


# ── command ───────────────────────────────────────────────────────────────────

@pytest.fixture
def policy_dir(tmp_path):
    (tmp_path / "verify-images.yaml").write_text(_POLICY, encoding="utf-8")
    return tmp_path


def test_default_mode_is_audit_and_applies(runner, policy_dir):
    with patch("cli.commands.admission_cmd.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        result = runner.invoke(admission_command, ["--policy-dir", str(policy_dir)])
    assert result.exit_code == 0, result.output
    assert "Audit" in result.output
    applied_text = mock_run.call_args.kwargs["input"]
    assert "validationFailureAction: Audit" in applied_text


def test_enforce_mode_warns_and_applies_enforce(runner, policy_dir):
    with patch("cli.commands.admission_cmd.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        result = runner.invoke(admission_command, ["--mode", "enforce", "--policy-dir", str(policy_dir)])
    assert result.exit_code == 0, result.output
    assert "BLOCKS" in result.output                       # the enforce warning
    assert "validationFailureAction: Enforce" in mock_run.call_args.kwargs["input"]


def test_dry_run_never_calls_kubectl(runner, policy_dir):
    with patch("cli.commands.admission_cmd.subprocess.run") as mock_run:
        result = runner.invoke(
            admission_command, ["--mode", "enforce", "--dry-run", "--policy-dir", str(policy_dir)]
        )
    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output
    mock_run.assert_not_called()


def test_missing_policy_dir_exits_one(runner, tmp_path):
    missing = tmp_path / "nope"
    result = runner.invoke(admission_command, ["--policy-dir", str(missing)])
    assert result.exit_code == 1
    # Rich may wrap the (long temp) path across lines, so normalize whitespace
    # before substring-matching the error text.
    assert "not found" in " ".join(result.output.split())


def test_networkpolicy_templates_are_skipped(runner, policy_dir):
    # networkpolicy-egress.yaml is a reference-only template, not a ClusterPolicy.
    (policy_dir / "networkpolicy-egress.yaml").write_text("kind: NetworkPolicy\n", encoding="utf-8")
    with patch("cli.commands.admission_cmd.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        result = runner.invoke(admission_command, ["--policy-dir", str(policy_dir)])
    assert result.exit_code == 0, result.output
    applied = [c.kwargs["input"] for c in mock_run.call_args_list]
    assert not any("NetworkPolicy" in text for text in applied)
    assert len(applied) == 1  # only verify-images.yaml
