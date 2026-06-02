"""
cli/commands/dashboard_cmd.py — `guardops dashboard` (Phase 13).

Runs the GuardOps web dashboard API locally with uvicorn. Same FastAPI app the
in-cluster Deployment serves (backend.dashboard.app:app), reading the same
.guardops.yaml. Zero infrastructure — great for development and demos.

The dashboard depends on the optional 'dashboard' extra (FastAPI + uvicorn); if it
is not installed we print the one-line install command rather than a traceback.

Examples:
  guardops dashboard                      # http://0.0.0.0:8081
  guardops dashboard --port 9000 --reload
"""

import importlib.util
import sys

import click

from cli.utils.output import console, error, header, info, warn
from cli.utils.config import load_config


@click.command("dashboard")
@click.option("--host", default=None, help="Bind host (default: dashboard.host in config, or 0.0.0.0).")
@click.option("--port", default=None, type=int, help="Bind port (default: dashboard.port in config, or 8081).")
@click.option("--reload", "reload_", is_flag=True, default=False,
              help="Auto-reload on code changes (development only).")
def dashboard_command(host, port, reload_):
    """Serve the GuardOps web dashboard API (FastAPI) locally."""
    if importlib.util.find_spec("uvicorn") is None or importlib.util.find_spec("fastapi") is None:
        # Escape the [ so Rich does not treat [dashboard] as a markup tag.
        error(
            "The dashboard needs the optional extra. Install it with:\n"
            "   [bold green]pip install 'guardops\\[dashboard]'[/bold green]"
        )
        sys.exit(1)

    config = load_config()
    dash = config.get("dashboard", {}) or {}
    host = host or dash.get("host", "0.0.0.0")
    port = port or int(dash.get("port", 8081))

    from backend.dashboard.settings import load_settings
    settings = load_settings(config)
    backend = (settings.config.get("metadata", {}) or {}).get("backend", "sqlite")

    header("GuardOps · Dashboard", f"Web dashboard API for {settings.project_name}")
    info(f"URL:          http://{host}:{port}")
    info(f"API base:     http://{host}:{port}/api/v1")
    info(f"OpenAPI docs: http://{host}:{port}/docs")
    info(f"Metadata:     backend=[cyan]{backend}[/cyan]")
    if settings.auth_enabled:
        info(f"Auth:         [cyan]{settings.auth_mode}[/cyan] (enabled)")
    else:
        warn(
            "Auth is DISABLED (no credential configured). For a public deployment set "
            "[cyan]GUARDOPS_DASHBOARD_TOKEN[/cyan] (or USER/PASSWORD for basic auth)."
        )
    console.print()

    import uvicorn
    uvicorn.run(
        "backend.dashboard.app:app",
        host=host,
        port=port,
        reload=reload_,
        log_level="info",
    )
