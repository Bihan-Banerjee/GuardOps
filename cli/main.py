"""
cli/main.py

This is the root of the CLI. Think of it as the trunk of a tree.
Every command (init, deploy, status, logs) is a branch attached to this trunk.

How Click works:
  @click.group() creates a command GROUP — a command that has subcommands.
  Running `guardops` alone shows help.
  Running `guardops init` invokes the init subcommand.
"""

import click
from rich.console import Console
from dotenv import load_dotenv

# Load .env file at startup so all os.environ calls work throughout the app.
# load_dotenv() looks for .env in the current dir and parent dirs.
# override=False means existing env vars are NOT overwritten (CI can set its own).
load_dotenv(override=False)

# Create a single Console instance for this module.
# Rich's Console handles all colored/styled terminal output.
# We import this in other files too (via cli.utils.output).
console = Console()

# Import all command functions. We import them HERE (not at the top)
# to avoid circular import issues. Each command module imports from utils,
# and utils should not import from main.
from cli.commands.init_cmd import init_command
from cli.commands.deploy_cmd import deploy_command
from cli.commands.status_cmd import status_command
from cli.commands.logs_cmd import logs_command


@click.group()
@click.version_option(
    version="0.1.0",
    prog_name="guardops",
    # This is the message shown when user runs `guardops --version`
    message="%(prog)s version %(version)s"
)
def cli():
    """
    GuardOps – Autonomous DevSecOps CLI Platform.

    Automates building, scanning, and deploying applications
    to Kubernetes with built-in security checks.

    Run `guardops COMMAND --help` for help on any command.
    """
    # This function body intentionally does nothing.
    # Click calls it before any subcommand runs.
    # In later phases, we'll add global flags here (--debug, --config-file).
    pass


# Attach each subcommand to the group.
# The string in add_command(..., name="...") is what the user types.
# guardops init    → runs init_command()
# guardops deploy  → runs deploy_command()
# guardops status  → runs status_command()
# guardops logs    → runs logs_command()
cli.add_command(init_command,   name="init")
cli.add_command(deploy_command, name="deploy")
cli.add_command(status_command, name="status")
cli.add_command(logs_command,   name="logs")


# This block runs ONLY when you execute: python cli/main.py
# It does NOT run when the module is imported.
# In production, Click calls cli() via the entry point defined in pyproject.toml.
if __name__ == "__main__":
    cli()