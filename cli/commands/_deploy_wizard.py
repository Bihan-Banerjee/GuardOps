"""
cli/commands/_deploy_wizard.py

Interactive "default vs custom" chooser for `guardops deploy` (Phase 13).

WHY THIS EXISTS:
  `guardops deploy` exposes eleven flags (--env, --slot, --gitops, --gitops-branch,
  --skip-scan, --skip-build, --skip-sonarqube, --skip-trivy, --skip-dast, --fail-on,
  --replicas). New and occasional users can't remember them. This module adds a
  friendly chooser that runs before the deploy pipeline:

    Default  → exactly today's behavior (no flags)
    Custom   → walk through the choices with prompts
    Cancel   → abort cleanly

  It also TEACHES the flags: after the wizard resolves the options it prints the
  equivalent one-line command, so next time the user can skip the wizard.

DESIGN PRINCIPLES (mirror the rest of the CLI):
  - A single DeployOptions dataclass carries the resolved values. Both the flag
    path and the wizard path produce one of these, so deploy_cmd has exactly one
    execution path (see deploy_cmd._execute_deploy).
  - CI-SAFE BY CONSTRUCTION: the wizard is only ever offered on an interactive
    terminal when no deploy flag was typed. Any explicit flag, --yes, a non-TTY
    stream, or a CI environment variable bypasses it entirely. morning-start.ps1
    passes --skip-scan, so it never triggers the wizard.
  - Prompting goes through the thin _ask/_confirm/_ask_int wrappers so tests can
    monkeypatch them with scripted answers without a real terminal.

The leading underscore keeps this out of the command namespace (it is not a
registered Click command), matching cli/commands/_metadata_common.py.
"""

import os
import sys
from dataclasses import dataclass
from typing import Optional

from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from rich import box

from cli.utils.output import console, info

# Click parameter names that mean "the user is driving this manually". If any of
# these was typed on the command line, we respect it and never show the wizard.
DEPLOY_FLAG_PARAMS = [
    "env", "slot", "use_gitops", "gitops_branch", "skip_scan", "skip_build",
    "skip_sonarqube", "skip_trivy", "skip_dast", "fail_on", "replicas",
]


@dataclass
class DeployOptions:
    """Resolved deploy parameters — the single source of truth for one deploy.

    Field names and defaults match the `guardops deploy` Click options exactly so
    a DeployOptions can be built directly from them and consumed unchanged by
    deploy_cmd._execute_deploy.
    """
    env: str = "local"
    slot: Optional[str] = None
    use_gitops: bool = False
    gitops_branch: str = "main"
    skip_scan: bool = False
    skip_build: bool = False
    skip_sonarqube: bool = False
    skip_trivy: bool = False
    skip_dast: bool = False
    fail_on: str = "HIGH"
    replicas: Optional[int] = None


# ── Prompt wrappers (monkeypatched in tests) ───────────────────────────────────

def _ask(prompt: str, **kwargs) -> str:
    return Prompt.ask(prompt, **kwargs)


def _confirm(prompt: str, *, default: bool = True) -> bool:
    return Confirm.ask(prompt, default=default)


def _ask_int(prompt: str, *, default: int) -> int:
    return IntPrompt.ask(prompt, default=default)


# ── Trigger gate ────────────────────────────────────────────────────────────--

def _is_ci() -> bool:
    """True in a CI runner. GitHub Actions sets both CI and GITHUB_ACTIONS."""
    return bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"))


def should_offer_wizard(ctx, interactive: bool, assume_yes: bool) -> bool:
    """Decide whether to show the interactive chooser.

    Rules (in order):
      1. --interactive/-i  → always show it (explicit opt-in).
      2. --yes/-y          → never show it (explicit opt-out, run defaults).
      3. CI environment    → never (headless).
      4. stdin/stdout not a TTY → never (piped / redirected / non-interactive).
      5. Any deploy flag typed on the command line → never (user is driving).
      6. Otherwise (bare `guardops deploy` in a terminal) → show it.
    """
    if interactive:
        return True
    if assume_yes:
        return False
    if _is_ci():
        return False
    try:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            return False
    except Exception:
        # A stream without isatty() (some test/pipe wrappers) is treated as non-interactive.
        return False

    # If the user explicitly typed any deploy flag, honor it without prompting.
    try:
        from click.core import ParameterSource
        for name in DEPLOY_FLAG_PARAMS:
            if ctx.get_parameter_source(name) == ParameterSource.COMMANDLINE:
                return False
    except Exception:
        # If Click can't report sources for some reason, fall back to showing the
        # wizard — we already know it's an interactive terminal with no --yes.
        pass

    return True


# ── The wizard ──────────────────────────────────────────────────────────────--

def run_deploy_wizard(config: dict, defaults: DeployOptions) -> Optional[DeployOptions]:
    """Run the default/custom/cancel chooser.

    Returns a resolved DeployOptions to proceed with, or None if the user cancels.
    `defaults` are the Click defaults (i.e. today's bare-`guardops deploy`), used
    both for the Default path and as the starting point for each Custom prompt.
    """
    project_name = config.get("project", {}).get("name", "your app")

    console.print()
    console.rule("[bold]GuardOps [cyan]Deploy[/cyan][/bold]  |  interactive setup")
    console.print(
        f"  Deploying [bold]{project_name}[/bold]. Choose how to proceed — or pass flags "
        "directly next time.\n"
        "  [dim]Default[/dim] = build + scan + deploy to local k3d (no flags). "
        "[dim]Custom[/dim] = pick each option."
    )
    console.print()

    mode = _ask(
        "  How would you like to deploy?",
        choices=["default", "custom", "cancel"],
        default="default",
    )

    if mode == "cancel":
        return None
    if mode == "default":
        opts = DeployOptions(**vars(defaults))
    else:
        opts = _run_custom(config, defaults)

    console.print()
    print_equivalent_command(opts)
    console.print()
    if not confirm_plan(opts):
        return None
    return opts


def _run_custom(config: dict, defaults: DeployOptions) -> DeployOptions:
    """Walk the user through every deploy option, returning a resolved DeployOptions."""
    opts = DeployOptions(**vars(defaults))

    # ── Environment ───────────────────────────────────────────────────────────
    opts.env = _ask(
        "  Target environment",
        choices=["local", "staging", "prod"],
        default=defaults.env,
    )

    # ── Build ─────────────────────────────────────────────────────────────────
    opts.skip_build = not _confirm(
        "  Build the Docker image now? (No = reuse the existing image)",
        default=not defaults.skip_build,
    )

    # ── Security scans ─────────────────────────────────────────────────────────
    run_scans = _confirm("  Run pre-deploy security scans?", default=not defaults.skip_scan)
    opts.skip_scan = not run_scans
    if run_scans:
        opts.skip_trivy = not _confirm(
            "    Include Trivy (CVE + secret + IaC scan)?", default=not defaults.skip_trivy
        )
        opts.skip_sonarqube = not _confirm(
            "    Include SonarQube (quality gate)?", default=not defaults.skip_sonarqube
        )
        opts.fail_on = _ask(
            "    Block the deploy at which severity or above?",
            choices=["LOW", "MEDIUM", "HIGH", "CRITICAL"],
            default=defaults.fail_on,
        )

    # ── DAST (prod only — ZAP needs a stable public URL) ───────────────────────
    if opts.env == "prod":
        opts.skip_dast = not _confirm(
            "  Run the post-deploy DAST scan (OWASP ZAP)?", default=not defaults.skip_dast
        )

    # ── Blue-green slot (staging/prod only) ────────────────────────────────────
    if opts.env in ("staging", "prod"):
        slot_choice = _ask(
            "  Blue-green slot to deploy into?",
            choices=["none", "blue", "green"],
            default=defaults.slot or "none",
        )
        opts.slot = None if slot_choice == "none" else slot_choice

    # ── GitOps (staging/prod, and not a slot deploy) ───────────────────────────
    if opts.env in ("staging", "prod") and not opts.slot:
        opts.use_gitops = _confirm(
            "  Use GitOps (commit image override + trigger ArgoCD sync)?",
            default=defaults.use_gitops,
        )
        if opts.use_gitops:
            opts.gitops_branch = _ask(
                "    Branch to commit the override to", default=defaults.gitops_branch
            )

    # ── Replica count ──────────────────────────────────────────────────────────
    default_replicas = defaults.replicas if defaults.replicas is not None else (2 if opts.env == "prod" else 1)
    if _confirm("  Override the replica count?", default=defaults.replicas is not None):
        opts.replicas = _ask_int("    Replicas", default=default_replicas)

    return opts


# ── Equivalent command (teaches the flags) ─────────────────────────────────────

def to_cli_args(opts: DeployOptions) -> list[str]:
    """The minimal flag list that reproduces `opts` — only non-default values."""
    args: list[str] = []
    if opts.env != "local":
        args += ["--env", opts.env]
    if opts.slot:
        args += ["--slot", opts.slot]
    if opts.use_gitops:
        args.append("--gitops")
        if opts.gitops_branch and opts.gitops_branch != "main":
            args += ["--gitops-branch", opts.gitops_branch]
    if opts.skip_build:
        args.append("--skip-build")
    if opts.skip_scan:
        args.append("--skip-scan")
    if opts.skip_trivy:
        args.append("--skip-trivy")
    if opts.skip_sonarqube:
        args.append("--skip-sonarqube")
    if opts.skip_dast:
        args.append("--skip-dast")
    if opts.fail_on and opts.fail_on != "HIGH":
        args += ["--fail-on", opts.fail_on]
    if opts.replicas is not None:
        args += ["--replicas", str(opts.replicas)]
    return args


def equivalent_command(opts: DeployOptions) -> str:
    """The full one-line command equivalent to `opts` (for display and logs)."""
    args = to_cli_args(opts)
    return "guardops deploy" + ((" " + " ".join(args)) if args else "")


def print_equivalent_command(opts: DeployOptions) -> None:
    """Show the equivalent flag command so the user learns to skip the wizard."""
    info(f"Next time, run this directly: [bold green]{equivalent_command(opts)}[/bold green]")


# ── Confirmation summary ────────────────────────────────────────────────────--

def confirm_plan(opts: DeployOptions) -> bool:
    """Render a summary table of the resolved options and ask for final go-ahead."""
    table = Table(box=box.ROUNDED, show_header=False, padding=(0, 2), border_style="dim")
    table.add_column("Setting", style="dim", justify="right")
    table.add_column("Value", style="bold")

    scans = "skipped" if opts.skip_scan else _scan_tools_label(opts)
    dast = "skipped" if opts.skip_dast else ("ZAP (prod)" if opts.env == "prod" else "n/a")

    table.add_row("Environment", opts.env)
    table.add_row("Build image", "no (reuse)" if opts.skip_build else "yes")
    table.add_row("Security scans", scans)
    if not opts.skip_scan:
        table.add_row("Block at", f"{opts.fail_on}+")
    table.add_row("DAST", dast)
    if opts.slot:
        table.add_row("Blue-green slot", opts.slot)
    if opts.env in ("staging", "prod"):
        table.add_row("GitOps", f"yes → {opts.gitops_branch}" if opts.use_gitops else "no")
    if opts.replicas is not None:
        table.add_row("Replicas", str(opts.replicas))

    console.print(table)
    console.print()
    return _confirm("  Proceed with this deploy?", default=True)


def _scan_tools_label(opts: DeployOptions) -> str:
    """Human-readable list of which scanners will run (Semgrep + Bandit always)."""
    tools = ["Semgrep", "Bandit"]
    if not opts.skip_trivy:
        tools.append("Trivy")
    if not opts.skip_sonarqube:
        tools.append("SonarQube")
    return ", ".join(tools)
