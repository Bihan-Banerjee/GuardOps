"""
cli/utils/config.py

Manages reading and writing the .guardops.yaml project config file.

WHY YAML: Human-readable, supports comments, widely used in DevOps
(Kubernetes, Helm, Docker Compose all use YAML). JSON doesn't support
comments, which hurts developer experience.

The config file (.guardops.yaml) is the "memory" of a GuardOps project.
It stores the project name, cloud settings, K8s namespace, etc.
Every command reads this file to know how to behave.

Phase 7 changes:
  - Added runtime_security section to DEFAULT_CONFIG
  - New keys: enabled, falco_alert_window, alert_fail_on, namespaces_to_watch
"""

import sys
from pathlib import Path
from typing import Any

import yaml

from cli.utils.output import error

# The config file is always in the current working directory.
# Path(".guardops.yaml") is equivalent to os.path.join(os.getcwd(), ".guardops.yaml")
# but cleaner and cross-platform.
CONFIG_FILENAME = ".guardops.yaml"


# ─── Default config structure ─────────────────────────────────────────────────
# This is what gets written when `guardops init` runs.
# Every key here is documented so users understand what to change.
DEFAULT_CONFIG: dict[str, Any] = {
    "project": {
        "name": "",           # Project name (e.g., "my-api")
        "description": "",    # Optional description
        "cloud": "local",     # "local" | "aws" | "gcp" | "azure"
    },
    "docker": {
        "dockerfile": "Dockerfile",   # Path to Dockerfile (relative to project root)
        "context": ".",               # Docker build context directory
        "registry": "",               # Registry URL (filled in for cloud deploys)
        "image_name": "",             # e.g., "my-api" (tag is added automatically)
    },
    "kubernetes": {
        "namespace": "default",       # K8s namespace to deploy into
        "cluster_context": "",        # kubectl context name (from ~/.kube/config)
        "deployment_name": "",        # Name of the K8s Deployment resource
    },
    "security": {
        "fail_on_severity": "HIGH",   # "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
        "tools": {
            "semgrep": True,
            "bandit": True,
            "trivy": True,
            "sonarqube": False,       # Needs external server; disabled by default
            "owasp_zap": False,       # Phase 6 DAST; needs running app
        },
    },
    "monitoring": {
        "grafana_url": "",
        "prometheus_url": "",
        "loki_url": "",               # Phase 7: Loki URL for guardops runtime-status
    },
    # ── Phase 7: Runtime Security ─────────────────────────────────────────────
    # Controls `guardops runtime-status` — queries Loki for Falco alerts.
    # Set enabled: true after running scripts/setup-runtime-security.ps1
    # and adding monitoring.loki_url to this file.
    "runtime_security": {
        "enabled": False,
        # Default time window for `guardops runtime-status` (passed as --since)
        "falco_alert_window": "1h",
        # Severity threshold for CI gate jobs (--fail-on default)
        "alert_fail_on": "CRITICAL",
        # Namespace filter — empty list means query all namespaces
        "namespaces_to_watch": [],
    },
}


def get_config_path() -> Path:
    """
    Returns the absolute path to .guardops.yaml in the current directory.
    Uses Path.cwd() which is the directory the user ran guardops from.
    """
    return Path.cwd() / CONFIG_FILENAME


def config_exists() -> bool:
    """Returns True if .guardops.yaml exists in current directory."""
    return get_config_path().exists()


def load_config() -> dict[str, Any]:
    """
    Reads .guardops.yaml and returns it as a Python dictionary.

    Exits with a helpful error if the file doesn't exist.
    This prevents confusing errors deep in the code when config is missing.

    yaml.safe_load() is safer than yaml.load() because it doesn't execute
    arbitrary Python objects embedded in YAML (a security risk).

    Returns:
        Dict containing the project configuration.
    """
    config_path = get_config_path()

    if not config_path.exists():
        error(
            f"No [cyan]{CONFIG_FILENAME}[/cyan] found in [bold]{Path.cwd()}[/bold].\n"
            f"   Run [bold green]guardops init[/bold green] first to set up your project."
        )
        sys.exit(1)

    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        error(f"{CONFIG_FILENAME} contains invalid YAML: {e}")
        sys.exit(1)

    if not config:
        error(f"{CONFIG_FILENAME} is empty. Run [bold]guardops init[/bold] to regenerate it.")
        sys.exit(1)

    return config


def save_config(config: dict[str, Any]) -> None:
    """
    Writes a config dict to .guardops.yaml.

    yaml.dump() converts a Python dict to YAML string.
    default_flow_style=False → uses block style (indented) not inline {a: b} style.
    allow_unicode=True       → preserves unicode characters in values.
    sort_keys=False          → keeps keys in the order we defined them (more readable).

    Args:
        config: Dictionary to write to the config file.
    """
    config_path = get_config_path()

    with open(config_path, "w") as f:
        yaml.dump(
            config,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            indent=2,         # 2-space indentation (standard for YAML)
        )


def get_project_name(config: dict) -> str:
    """
    Safely retrieves project name from config.
    Uses dict.get() with nested access to avoid KeyError on malformed config.
    """
    return config.get("project", {}).get("name", "unknown")


def merge_with_defaults(user_config: dict) -> dict:
    """
    Merges user config with defaults so missing keys don't cause KeyError.

    Python's dict doesn't deep-merge natively, so we do it manually.
    This lets users have a minimal .guardops.yaml and still have all keys work.

    Example:
        defaults  = {"a": {"x": 1, "y": 2}}
        user      = {"a": {"x": 99}}
        result    = {"a": {"x": 99, "y": 2}}   ← y is filled from defaults
    """
    result = DEFAULT_CONFIG.copy()
    for key, value in user_config.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            # Recursively merge nested dicts
            result[key] = {**result[key], **value}
        else:
            result[key] = value
    return result
