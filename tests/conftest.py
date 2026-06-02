"""
tests/conftest.py

Shared fixtures for the Phase 12 scan-metadata tests. Provides small factories for
building SecurityFinding / ConsolidatedReport objects so each test can describe a
scan in a couple of lines instead of constructing dataclasses by hand.
"""

import pytest
from click.testing import CliRunner

from backend.security.semgrep_runner import ScanResult, SecurityFinding
from backend.security.report_generator import ConsolidatedReport


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def make_finding():
    """Factory for SecurityFinding with sensible defaults."""
    def _make(
        severity="HIGH",
        tool="trivy",
        rule_id="RULE-1",
        cve="",
        message="example finding",
        file_path="app.py",
        line_start=1,
        fix_guidance="",
    ):
        return SecurityFinding(
            tool=tool,
            rule_id=rule_id,
            severity=severity,
            message=message,
            file_path=file_path,
            line_start=line_start,
            line_end=line_start,
            cve=cve,
            fix_guidance=fix_guidance,
        )
    return _make


@pytest.fixture
def make_report():
    """Factory for a ConsolidatedReport with one tool's findings.

    Pass `findings=[...]` to populate it; `tool` sets the scanner name on the
    single ScanResult so tool_runs has a row.
    """
    def _make(
        project="demo",
        image="demo:latest",
        timestamp="2026-05-01T10:00:00",
        fail_on="HIGH",
        findings=None,
        tool="trivy",
        success=True,
        skipped=False,
    ):
        scan_result = ScanResult(
            tool=tool,
            success=success,
            findings=list(findings or []),
            skipped=skipped,
        )
        return ConsolidatedReport(
            project_name=project,
            image_ref=image,
            timestamp=timestamp,
            scan_results=[scan_result],
            fail_on_severity=fail_on,
        )
    return _make
