import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from backend.security.semgrep_runner import ScanResult, SecurityFinding


@dataclass
class ConsolidatedReport:
    project_name: str
    image_ref: str
    timestamp: str
    scan_results: list[ScanResult]
    fail_on_severity: str = "HIGH"

    @property
    def all_findings(self) -> list[SecurityFinding]:
        findings = []
        for result in self.scan_results:
            findings.extend(result.findings)
        return findings

    @property
    def blocked(self) -> bool:
        return any(
            r.has_blocking_findings(self.fail_on_severity)
            for r in self.scan_results
            if not r.skipped
        )

    @property
    def severity_counts(self) -> dict[str, int]:
        counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for f in self.all_findings:
            if f.severity in counts:
                counts[f.severity] += 1
        return counts

    def to_dict(self) -> dict:
        return {
            "project_name": self.project_name,
            "image_ref": self.image_ref,
            "timestamp": self.timestamp,
            "blocked": self.blocked,
            "fail_on_severity": self.fail_on_severity,
            "severity_counts": self.severity_counts,
            "tools_run": [
                {
                    "tool": r.tool,
                    "success": r.success,
                    "skipped": r.skipped,
                    "skip_reason": r.skip_reason,
                    "critical": r.critical_count,
                    "high": r.high_count,
                    "medium": r.medium_count,
                    "low": r.low_count,
                    "error": r.error_message,
                }
                for r in self.scan_results
            ],
            "findings": [
                {
                    "tool": f.tool,
                    "rule_id": f.rule_id,
                    "severity": f.severity,
                    "message": f.message,
                    "file_path": f.file_path,
                    "line_start": f.line_start,
                    "cve": f.cve,
                    "fix_guidance": f.fix_guidance,
                    "code_snippet": f.code_snippet[:200] if f.code_snippet else "",
                }
                for f in sorted(
                    self.all_findings,
                    key=lambda x: ["CRITICAL", "HIGH", "MEDIUM", "LOW"].index(x.severity)
                )
            ],
        }


def generate_report(
    scan_results: list[ScanResult],
    project_name: str,
    image_ref: str,
    output_dir: str = "security/reports",
    fail_on_severity: str = "HIGH",
) -> ConsolidatedReport:
    report = ConsolidatedReport(
        project_name=project_name,
        image_ref=image_ref,
        timestamp=datetime.utcnow().isoformat(),
        scan_results=scan_results,
        fail_on_severity=fail_on_severity,
    )

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    timestamp_slug = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    json_path = output_path / f"report_{timestamp_slug}.json"
    json_path.write_text(json.dumps(report.to_dict(), indent=2))

    html_path = output_path / f"report_{timestamp_slug}.html"
    html_path.write_text(_render_html(report))

    latest_json = output_path / "latest.json"
    latest_json.write_text(json.dumps(report.to_dict(), indent=2))

    latest_html = output_path / "latest.html"
    latest_html.write_text(_render_html(report))

    return report


def _render_html(report: ConsolidatedReport) -> str:
    counts = report.severity_counts
    status_color = "#e74c3c" if report.blocked else "#27ae60"
    status_text = "BLOCKED — Fix issues before deploying" if report.blocked else "PASSED — Safe to deploy"

    severity_colors = {
        "CRITICAL": "#8e1c1c",
        "HIGH": "#c0392b",
        "MEDIUM": "#e67e22",
        "LOW": "#f1c40f",
    }

    findings_rows = ""
    for f in sorted(
        report.all_findings,
        key=lambda x: ["CRITICAL", "HIGH", "MEDIUM", "LOW"].index(x.severity)
    ):
        color = severity_colors.get(f.severity, "#ccc")
        snippet = f.code_snippet[:120].replace("<", "&lt;").replace(">", "&gt;") if f.code_snippet else ""
        findings_rows += f"""
        <tr>
          <td><span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:12px">{f.severity}</span></td>
          <td>{f.tool}</td>
          <td>{f.rule_id}</td>
          <td>{f.file_path}:{f.line_start}</td>
          <td>{f.message[:100]}</td>
          <td>{f.cve or "—"}</td>
          <td style="font-size:11px;color:#555">{f.fix_guidance[:80] if f.fix_guidance else "—"}</td>
        </tr>
        {"<tr><td colspan='7'><pre style='background:#f5f5f5;padding:8px;font-size:11px;overflow:auto'>" + snippet + "</pre></td></tr>" if snippet else ""}
        """

    tool_summary_rows = ""
    for tool_result in report.scan_results:
        status = "Skipped" if tool_result.skipped else ("Error" if not tool_result.success else "OK")
        status_c = "#888" if tool_result.skipped else ("#e74c3c" if not tool_result.success else "#27ae60")
        tool_summary_rows += f"""
        <tr>
          <td>{tool_result.tool}</td>
          <td><span style="color:{status_c}">{status}</span></td>
          <td style="color:#8e1c1c">{tool_result.critical_count}</td>
          <td style="color:#c0392b">{tool_result.high_count}</td>
          <td style="color:#e67e22">{tool_result.medium_count}</td>
          <td style="color:#888">{tool_result.low_count}</td>
          <td style="font-size:11px;color:#666">{tool_result.skip_reason or tool_result.error_message or "—"}</td>
        </tr>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>GuardOps Security Report — {report.project_name}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f0f2f5; color: #222; }}
  .container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
  h1 {{ font-size: 24px; font-weight: 600; margin-bottom: 4px; }}
  h2 {{ font-size: 16px; font-weight: 600; margin: 24px 0 12px; color: #444; }}
  .meta {{ color: #666; font-size: 13px; margin-bottom: 20px; }}
  .status-banner {{ padding: 14px 20px; border-radius: 8px; color: #fff; font-weight: 600; font-size: 15px; background: {status_color}; margin-bottom: 24px; }}
  .counts {{ display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }}
  .count-card {{ padding: 16px 24px; border-radius: 8px; color: #fff; min-width: 100px; text-align: center; }}
  .count-card .num {{ font-size: 32px; font-weight: 700; }}
  .count-card .label {{ font-size: 12px; opacity: 0.85; margin-top: 2px; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  th {{ background: #f8f9fa; padding: 10px 12px; text-align: left; font-size: 12px; color: #555; font-weight: 600; border-bottom: 1px solid #e9ecef; }}
  td {{ padding: 10px 12px; font-size: 13px; border-bottom: 1px solid #f0f0f0; vertical-align: top; word-break: break-word; }}
  tr:last-child td {{ border-bottom: none; }}
  .section {{ background: #fff; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
</style>
</head>
<body>
<div class="container">
  <h1>GuardOps Security Report</h1>
  <div class="meta">
    Project: <strong>{report.project_name}</strong> &nbsp;|&nbsp;
    Image: <strong>{report.image_ref}</strong> &nbsp;|&nbsp;
    Scanned: {report.timestamp} UTC
  </div>

  <div class="status-banner">{status_text}</div>

  <div class="counts">
    <div class="count-card" style="background:#8e1c1c"><div class="num">{counts["CRITICAL"]}</div><div class="label">CRITICAL</div></div>
    <div class="count-card" style="background:#c0392b"><div class="num">{counts["HIGH"]}</div><div class="label">HIGH</div></div>
    <div class="count-card" style="background:#e67e22"><div class="num">{counts["MEDIUM"]}</div><div class="label">MEDIUM</div></div>
    <div class="count-card" style="background:#95a5a6"><div class="num">{counts["LOW"]}</div><div class="label">LOW</div></div>
    <div class="count-card" style="background:#2c3e50"><div class="num">{len(report.all_findings)}</div><div class="label">TOTAL</div></div>
  </div>

  <div class="section">
    <h2>Tool Summary</h2>
    <table>
      <thead><tr><th>Tool</th><th>Status</th><th>Critical</th><th>High</th><th>Medium</th><th>Low</th><th>Notes</th></tr></thead>
      <tbody>{tool_summary_rows}</tbody>
    </table>
  </div>

  <div class="section">
    <h2>All Findings ({len(report.all_findings)})</h2>
    <table>
      <thead><tr><th>Severity</th><th>Tool</th><th>Rule</th><th>Location</th><th>Message</th><th>CVE</th><th>Fix</th></tr></thead>
      <tbody>{findings_rows if findings_rows else "<tr><td colspan='7' style='text-align:center;color:#27ae60;padding:20px'>No findings — clean scan</td></tr>"}</tbody>
    </table>
  </div>
</div>
</body>
</html>"""