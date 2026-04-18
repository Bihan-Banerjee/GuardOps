"""
cli/commands/init_cmd.py

Implements `guardops init`.

What this command does:
  1. Checks that required tools are installed (docker, kubectl)
  2. Asks the user questions (project name, cloud, namespace)
  3. Creates .guardops.yaml in the current directory
  4. Gives the user clear next-step instructions

Design principle: init should be SAFE to re-run. If .guardops.yaml
already exists, it asks before overwriting (to prevent data loss).
"""

import click
from pathlib import Path

from cli.utils.output import (
    header, success, info, warn, error, blank, section, console
)
from cli.utils.config import (
    config_exists,
    save_config,
    DEFAULT_CONFIG,
    CONFIG_FILENAME,
)
from cli.utils.system import check_all_dependencies, get_command_output


# Required tools for Phase 1 (local deploy mode)
PHASE1_DEPENDENCIES = [
    ("docker",  "Install Docker Desktop: https://docs.docker.com/get-docker/"),
    ("kubectl", "Install kubectl: https://kubernetes.io/docs/tasks/tools/"),
]


@click.command()
@click.option(
    "--name", "-n",
    default=None,                         # None means "ask the user" (prompt below)
    help="Project name (alphanumeric, hyphens allowed)"
)
@click.option(
    "--cloud",
    type=click.Choice(["local", "aws", "gcp"], case_sensitive=False),
    default="local",
    help="Cloud provider (use 'local' for Phase 1 development)"
)
@click.option(
    "--namespace", "-ns",
    default="default",
    help="Kubernetes namespace to deploy into"
)
@click.option(
    "--force", "-f",
    is_flag=True,                         # is_flag=True means it's a boolean toggle
    default=False,
    help="Overwrite existing .guardops.yaml without asking"
)
def init_command(name, cloud, namespace, force):
    """
    Initialize a new GuardOps project in the current directory.

    Creates a .guardops.yaml configuration file and validates
    that all required tools are installed.
    """
    header(
        "GuardOps · Init",
        f"Setting up project in {Path.cwd()}"
    )

    # ── Step 1: Check dependencies ────────────────────────────────────────────
    section("Checking dependencies")

    all_ok = check_all_dependencies(PHASE1_DEPENDENCIES)
    if not all_ok:
        blank()
        error("Please install the missing tools and run [bold]guardops init[/bold] again.")
        raise SystemExit(1)

    # Show the version of docker we found (useful for debugging environment issues)
    docker_version = get_command_output(
        ["docker", "--version"],
        default="unknown version"
    )
    kubectl_version = get_command_output(
        ["kubectl", "version", "--client", "--short"],
        default="unknown version"
    )
    info(f"Docker:  {docker_version}")
    info(f"kubectl: {kubectl_version}")
    blank()

    # ── Step 2: Check if config already exists ────────────────────────────────
    if config_exists() and not force:
        warn(f"[bold]{CONFIG_FILENAME}[/bold] already exists in this directory.")
        # click.confirm() blocks and waits for y/n input.
        # abort=True means pressing 'n' raises Abort (which Click handles cleanly).
        if not click.confirm("  Overwrite it?", default=False):
            info("Keeping existing config. Run with [bold]--force[/bold] to overwrite.")
            return

    # ── Step 3: Gather project info ───────────────────────────────────────────
    section("Project configuration")

    # If --name was not passed on the command line, prompt interactively.
    if name is None:
        # Default suggestion: current directory name (a sensible project name)
        default_name = Path.cwd().name.lower().replace(" ", "-").replace("_", "-")
        name = click.prompt(
            "  Project name",
            default=default_name,
        )

    # Validate project name: only lowercase letters, numbers, hyphens
    import re
    if not re.match(r'^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$|^[a-z0-9]$', name):
        error(
            "Project name must be lowercase alphanumeric with optional hyphens "
            "(e.g., 'my-api', 'webapp2'). No spaces or underscores."
        )
        raise SystemExit(1)

    # If running in local mode, try to detect the current kubectl context
    cluster_context = ""
    if cloud == "local":
        cluster_context = get_command_output(
            ["kubectl", "config", "current-context"],
            default=""
        )
        if cluster_context:
            info(f"Detected kubectl context: [cyan]{cluster_context}[/cyan]")
        else:
            warn("No kubectl context found. Make sure a local cluster is running.")
            warn("Try: [bold]k3d cluster create guardops-local[/bold]")

    # ── Step 4: Build config dict ─────────────────────────────────────────────
    # Start with defaults (so no key is ever missing) and override with user values
    config = DEFAULT_CONFIG.copy()

    # Deep-copy nested dicts to avoid mutating the DEFAULT_CONFIG constant
    config["project"] = {
        **DEFAULT_CONFIG["project"],
        "name": name,
        "cloud": cloud,
    }
    config["docker"] = {
        **DEFAULT_CONFIG["docker"],
        "image_name": name,
        # For AWS, registry will be filled in with ECR URL later
        "registry": "local" if cloud == "local" else "",
    }
    config["kubernetes"] = {
        **DEFAULT_CONFIG["kubernetes"],
        "namespace": namespace,
        "cluster_context": cluster_context,
        "deployment_name": name,
    }

    # ── Step 5: Write config file ─────────────────────────────────────────────
    section("Writing configuration")
    save_config(config)

    success(f"Created [bold cyan]{CONFIG_FILENAME}[/bold cyan]")

    # ── Step 6: Create supporting files if they don't exist ──────────────────
    _create_sample_dockerfile_if_missing(name)
    _create_env_example_if_missing()

    # ── Step 7: Print next steps ──────────────────────────────────────────────
    blank()
    section("Next steps")
    steps = [
        f"1. Review [cyan]{CONFIG_FILENAME}[/cyan] and adjust settings",
        "2. Ensure a local Kubernetes cluster is running:",
        "   [dim]k3d cluster create guardops-local[/dim]",
        "3. Deploy your application:",
        f"   [bold green]guardops deploy[/bold green]",
        "4. Check deployment status:",
        f"   [bold green]guardops status[/bold green]",
    ]
    for step in steps:
        console.print(f"   {step}")
    blank()


def _create_sample_dockerfile_if_missing(project_name: str) -> None:
    """
    Creates a minimal sample Dockerfile if one doesn't exist.
    This lets users run `guardops deploy` immediately after init
    without needing their own Dockerfile first.
    """
    dockerfile_path = Path.cwd() / "Dockerfile"
    if dockerfile_path.exists():
        return

    # This is a production-quality multi-stage Dockerfile for a Python app.
    # Phase 1: using a simple single-stage for clarity.
    # Phase 3+ will use multi-stage builds.
    dockerfile_content = f'''# Generated by GuardOps init
# Replace this with your application's actual Dockerfile

FROM python:3.11-slim

# Set working directory inside the container
WORKDIR /app

# Install dependencies first (separate layer for Docker cache efficiency)
# If only requirements.txt changes, Docker rebuilds from here.
# If only source code changes, this layer is cached.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt 2>/dev/null || true

# Copy application source code
COPY . .

# Run as non-root user for security (see Phase 3 for full security hardening)
RUN useradd --no-create-home --shell /bin/false appuser
USER appuser

# Expose the port your app listens on
EXPOSE 8080

# Healthcheck: Docker will run this every 30s to check if container is healthy
# Replace /healthz with your actual health endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \\
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')" || exit 1

# The command that runs when the container starts
# Replace this with your actual startup command
CMD ["python", "-m", "http.server", "8080"]
'''
    dockerfile_path.write_text(dockerfile_content)
    info(f"Created sample [cyan]Dockerfile[/cyan] — replace with your app's Dockerfile")


def _create_env_example_if_missing() -> None:
    """
    Creates a .env.example file documenting expected environment variables.
    The actual .env file (with real secrets) is never committed to git.
    """
    env_example_path = Path.cwd() / ".env.example"
    if env_example_path.exists():
        return

    content = """# GuardOps Environment Variables
# Copy this file to .env and fill in your values.
# NEVER commit .env to git.

# AWS (Phase 2+)
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION=us-east-1
AWS_ACCOUNT_ID=

# Container Registry (Phase 2+)
ECR_REGISTRY=

# Security Tools (Phase 2+)
SEMGREP_APP_TOKEN=
SONAR_TOKEN=
SONAR_HOST_URL=

# Notifications (Phase 5+)
SLACK_WEBHOOK_URL=
ALERT_EMAIL=

# GuardOps internal
GUARDOPS_S3_BUCKET=guardops-reports
"""
    env_example_path.write_text(content)

    # Also add .env to .gitignore to prevent accidental secret commits
    gitignore_path = Path.cwd() / ".gitignore"
    gitignore_additions = ["\n# GuardOps\n.env\n.guardops-state.json\nsecurity/reports/\n"]
    if gitignore_path.exists():
        existing = gitignore_path.read_text()
        if ".env" not in existing:
            with open(gitignore_path, "a") as f:
                f.writelines(gitignore_additions)
    else:
        gitignore_path.write_text("".join(gitignore_additions))

    info("Created [cyan].env.example[/cyan] — copy to [cyan].env[/cyan] and fill in secrets")