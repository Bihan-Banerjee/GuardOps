import sys
from pathlib import Path

import click
from rich.table import Table
from rich import box

from cli.utils.output import (
    header, section, success, info, warn, error,
    blank, console, result_panel,
)
from cli.utils.config import load_config, get_project_name
from backend.security.semgrep_runner import run_semgrep, ScanResult
from backend.security.bandit_runner import run_bandit
from backend.security.trivy_runner import run_trivy_image, run_trivy_filesystem
from backend.security.sonarqube_runner import run_sonarqube, check_quality_gate
from backend.security.report_generator import generate_report


SEVERITY_STYLE = {
    "CRITICAL": "bold red",
    "HIGH":     "red",
    "MEDIUM":   "yellow",
    "LOW":      "dim",
}

SEVERITY_ICON = {
    "CRITICAL": "●",
    "HIGH":     "●",
    "MEDIUM":   "◐",
    "LOW":      "○",
}


@click.command()
@click.option("--image", "-i", default=None, help="Docker image ref for Trivy container scan (e.g. test-app:latest)")
@click.option("--path", "-p", default=".", help="Source path to scan (default: current directory)")
@click.option("--fail-on", default="HIGH", type=click.Choice(["LOW", "MEDIUM", "HIGH", "CRITICAL"]), help="Minimum severity that fails the scan")
@click.option("--skip-semgrep",    is_flag=True, default=False)
@click.option("--skip-bandit",     is_flag=True, default=False)
@click.option("--skip-trivy",      is_flag=True, default=False)
@click.option("--skip-sonarqube",  is_flag=True, default=False)
@click.option("--report-dir",      default="security/reports", help="Directory to write HTML and JSON reports")
@click.option("--no-report",       is_flag=True, default=False, help="Skip writing report files")
def scan_command(image, path, fail_on, skip_semgrep, skip_bandit, skip_trivy, skip_sonarqube, report_dir, no_report):
    """
    Run all security scans against the project source and/or Docker image.
    Fails with exit code 1 if findings meet or exceed --fail-on severity.
    """
    config = load_config()
    project_name = get_project_name(config)
    image_ref = image or f"{project_name}:latest"

    header(
        "GuardOps · Security Scan",
        f"Project: {project_name}  |  Fail on: {fail_on}  |  Path: {path}"
    )

    scan_results = []

    # ── Semgrep ───────────────────────────────────────────────────────────────
    if not skip_semgrep:
        section("Semgrep — SAST")
        result = run_semgrep(path, config)
        scan_results.append(result)
        _print_tool_result(result)

    # ── Bandit ────────────────────────────────────────────────────────────────
    if not skip_bandit:
        section("Bandit — Python security")
        result = run_bandit(path, config)
        scan_results.append(result)
        _print_tool_result(result)

    # ── Trivy filesystem ──────────────────────────────────────────────────────
    if not skip_trivy:
        section("Trivy — filesystem secrets and misconfiguration")
        fs_result = run_trivy_filesystem(path, config)
        scan_results.append(fs_result)
        _print_tool_result(fs_result)

        if image:
            section(f"Trivy — container scan ({image_ref})")
            img_result = run_trivy_image(image_ref, config)
            scan_results.append(img_result)
            _print_tool_result(img_result)

    # ── SonarQube ─────────────────────────────────────────────────────────────
    if not skip_sonarqube:
        section("SonarQube — code quality and security")
        result = run_sonarqube(path, config)
        scan_results.append(result)
        _print_tool_result(result)

        if not result.skipped and result.success:
            gate_passed, gate_msg = check_quality_gate(config)
            if gate_passed:
                success(f"Quality gate: {gate_msg}")
            else:
                error(f"Quality gate: {gate_msg}")

    # ── Generate report ───────────────────────────────────────────────────────
    blank()
    section("Report")

    if not no_report:
        report = generate_report(
            scan_results=scan_results,
            project_name=project_name,
            image_ref=image_ref,
            output_dir=report_dir,
            fail_on_severity=fail_on,
        )
        success(f"Reports written to [cyan]{report_dir}/[/cyan]")
        info(f"HTML report: [cyan]{report_dir}/latest.html[/cyan]")
        info(f"JSON report: [cyan]{report_dir}/latest.json[/cyan]")
    else:
        from backend.security.report_generator import ConsolidatedReport
        from datetime import datetime
        report = ConsolidatedReport(
            project_name=project_name,
            image_ref=image_ref,
            timestamp=datetime.utcnow().isoformat(),
            scan_results=scan_results,
            fail_on_severity=fail_on,
        )

    # ── Final summary table ───────────────────────────────────────────────────
    blank()
    _print_summary_table(scan_results)

    counts = report.severity_counts
    total = sum(counts.values())

    if report.blocked:
        blank()
        result_panel(
            "Scan FAILED — deployment blocked",
            [
                f"[bold]Critical:[/bold] {counts['CRITICAL']}   [bold]High:[/bold] {counts['HIGH']}   [bold]Medium:[/bold] {counts['MEDIUM']}   [bold]Low:[/bold] {counts['LOW']}",
                f"Pipeline is blocked because {fail_on}+ severity findings were found.",
                f"Fix the findings above then re-run [bold]guardops scan[/bold].",
            ],
            style="red",
        )
        sys.exit(1)
    else:
        result_panel(
            "Scan PASSED",
            [
                f"[bold]Total findings:[/bold] {total}  (none at {fail_on} or above)",
                f"Critical: {counts['CRITICAL']}   High: {counts['HIGH']}   Medium: {counts['MEDIUM']}   Low: {counts['LOW']}",
                "Safe to proceed with deployment.",
            ],
            style="green",
        )


def _print_tool_result(result: ScanResult) -> None:
    if result.skipped:
        warn(f"[dim]{result.tool}[/dim] skipped — {result.skip_reason}")
        return

    if not result.success:
        error(f"{result.tool} error — {result.error_message}")
        return

    total = len(result.findings)
    if total == 0:
        success(f"{result.tool} — no findings")
        return

    warn(
        f"{result.tool} — "
        f"[red]{result.critical_count} critical[/red]  "
        f"[red]{result.high_count} high[/red]  "
        f"[yellow]{result.medium_count} medium[/yellow]  "
        f"{result.low_count} low"
    )

    top_findings = sorted(
        result.findings,
        key=lambda f: ["CRITICAL", "HIGH", "MEDIUM", "LOW"].index(f.severity)
    )[:5]

    for f in top_findings:
        style = SEVERITY_STYLE.get(f.severity, "")
        icon = SEVERITY_ICON.get(f.severity, "○")
        location = f"{f.file_path}:{f.line_start}" if f.file_path else "—"
        console.print(
            f"   [{style}]{icon} {f.severity:<8}[/{style}] "
            f"[dim]{f.tool}[/dim]  {f.message[:60]}  [dim]{location}[/dim]"
        )

    if total > 5:
        console.print(f"   [dim]... and {total - 5} more. See report for full list.[/dim]")
    blank()


def _print_summary_table(results: list[ScanResult]) -> None:
    table = Table(
        title="Scan Summary",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold dim",
        padding=(0, 1),
    )
    table.add_column("Tool",     style="bold", min_width=14)
    table.add_column("Status",   justify="center", min_width=10)
    table.add_column("Critical", justify="right", style="red")
    table.add_column("High",     justify="right", style="red")
    table.add_column("Medium",   justify="right", style="yellow")
    table.add_column("Low",      justify="right", style="dim")

    for r in results:
        if r.skipped:
            status = "[dim]skipped[/dim]"
        elif not r.success:
            status = "[red]error[/red]"
        elif r.critical_count > 0 or r.high_count > 0:
            status = "[red]issues[/red]"
        elif r.medium_count > 0:
            status = "[yellow]warnings[/yellow]"
        else:
            status = "[green]clean[/green]"

        table.add_row(
            r.tool,
            status,
            str(r.critical_count) if not r.skipped else "—",
            str(r.high_count)     if not r.skipped else "—",
            str(r.medium_count)   if not r.skipped else "—",
            str(r.low_count)      if not r.skipped else "—",
        )

    console.print(table)