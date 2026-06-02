"""
cli/commands/_metadata_common.py

Shared helpers for the Phase 12 metadata query commands
(history / findings / trends / diff / db). Not a registered command — the leading
underscore keeps it out of the command namespace.

Keeps the five command modules consistent: one place for severity styling, the
config→store handoff, JSON emission, and timestamp/text formatting.
"""

import json
import sys
from typing import NoReturn

import click

from backend.metadata.base import SEVERITY_ORDER
from backend.metadata.factory import get_store
from cli.utils.config import load_config
from cli.utils.output import error

# Matches scan_cmd.SEVERITY_STYLE so stored findings render with the same colors
# as a live scan.
SEVERITY_STYLE = {
    "CRITICAL": "bold red",
    "HIGH":     "red",
    "MEDIUM":   "yellow",
    "LOW":      "dim",
}


def open_store():
    """Load .guardops.yaml and return (config, store).

    load_config() exits 1 with a helpful message when run outside a project, so
    these commands behave like every other guardops command.
    """
    config = load_config()
    return config, get_store(config)


def severity_cell(severity: str) -> str:
    """A severity string wrapped in its Rich style for table cells."""
    style = SEVERITY_STYLE.get(severity, "")
    return f"[{style}]{severity}[/{style}]" if style else severity


def severity_rank(severity: str) -> int:
    """Sort key: higher = more severe. Unknown severities sort last."""
    try:
        return SEVERITY_ORDER.index(severity)
    except ValueError:
        return -1


def fmt_ts(timestamp: str) -> str:
    """ISO-8601 → 'YYYY-MM-DD HH:MM:SS' for compact table display."""
    if not timestamp:
        return "—"
    return timestamp[:19].replace("T", " ")


def short(text, width: int) -> str:
    """Truncate with an ellipsis so wide columns (image refs, messages) stay tidy."""
    if text is None:
        return ""
    text = str(text)
    return text if len(text) <= width else text[: width - 1] + "…"


def emit_json(obj) -> None:
    """Print JSON for --json-output mode via click.echo (no Rich markup interference)."""
    click.echo(json.dumps(obj, indent=2, default=str))


def fail(message: str) -> NoReturn:
    """Print an error and exit 1 — for genuine DB read failures (corrupt/locked DB)."""
    error(message)
    sys.exit(1)
