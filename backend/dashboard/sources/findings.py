"""
backend/dashboard/sources/findings.py

Durable scan data for the dashboard. Reads through whatever MetadataStore is
configured (SqliteMetadataStore locally, S3MetadataStore in-cluster), so the same
queries power both. list_runs / get_run / query_findings / severity_trends are
called directly by the routes; this module adds the two derived views:

  compute_summary — the landing-page cards (latest run, severity totals, gate
                    pass-rate, per-tool breakdown).
  compute_diff    — new-vs-fixed between two runs, mirroring `guardops diff`
                    (cli/commands/diff_cmd.py): fingerprint set-diff, blocking =
                    new CRITICAL/HIGH.
"""

_BLOCKING = {"CRITICAL", "HIGH"}
_ALL = 1_000_000  # compare full finding sets, not a page


def compute_summary(store, project=None, recent: int = 200) -> dict:
    """Aggregate the most recent runs into dashboard landing cards."""
    runs = store.list_runs(project=project, limit=recent)
    empty: dict = {
        "total_runs": 0,
        "latest": None,
        "by_severity": {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0},
        "gate_pass_rate": None,
        "by_tool": [],
        "recent": [],
    }
    if not runs:
        return empty

    latest = runs[0]
    passed = sum(1 for r in runs if not r.blocked)

    by_tool: dict[str, int] = {}
    for f in store.query_findings(run_id=latest.id, limit=_ALL):
        by_tool[f.tool] = by_tool.get(f.tool, 0) + 1

    return {
        "total_runs": len(runs),
        "latest": latest.to_dict(),
        "by_severity": {
            "CRITICAL": latest.crit_count,
            "HIGH": latest.high_count,
            "MEDIUM": latest.medium_count,
            "LOW": latest.low_count,
        },
        "gate_pass_rate": round(passed / len(runs), 3),
        "by_tool": [
            {"tool": t, "count": c}
            for t, c in sorted(by_tool.items(), key=lambda kv: kv[1], reverse=True)
        ],
        "recent": [r.to_dict() for r in runs[:10]],
    }


def compute_diff(store, from_id=None, to_id=None, project=None) -> dict:
    """New-vs-fixed findings between two runs. Raises LookupError if an explicit
    run id is missing. Returns {"empty": True, ...} when there is nothing to diff."""
    if to_id is not None:
        to_run = store.get_run(to_id)
        if to_run is None:
            raise LookupError(f"run #{to_id} not found")
    else:
        recent = store.list_runs(project=project, limit=1)
        if not recent:
            return {"empty": True, "reason": "no scan runs recorded yet"}
        to_run = recent[0]

    if from_id is not None:
        from_run = store.get_run(from_id)
        if from_run is None:
            raise LookupError(f"run #{from_id} not found")
    else:
        from_run = store.latest_run_before(
            run_id=to_run.id, project=project or to_run.project_name
        )
        if from_run is None:
            return {
                "empty": True,
                "reason": "no earlier run to compare against",
                "to_run": to_run.to_dict(),
            }

    to_fp = {f.fingerprint: f for f in store.query_findings(run_id=to_run.id, limit=_ALL)}
    from_fp = {f.fingerprint: f for f in store.query_findings(run_id=from_run.id, limit=_ALL)}
    new = [f for fp, f in to_fp.items() if fp not in from_fp]
    fixed = [f for fp, f in from_fp.items() if fp not in to_fp]
    new_blocking = [f for f in new if f.severity in _BLOCKING]

    return {
        "from_run": from_run.to_dict(),
        "to_run": to_run.to_dict(),
        "new": [f.to_dict() for f in new],
        "fixed": [f.to_dict() for f in fixed],
        "new_blocking": len(new_blocking),
        "regressed": bool(new_blocking),
    }
