"""
cli/main.py — Root Click group. Registers all guardops subcommands.
"""

import click
from cli import __version__

from cli.commands.init_cmd import init_command
from cli.commands.deploy_cmd import deploy_command
from cli.commands.status_cmd import status_command
from cli.commands.logs_cmd import logs_command
from cli.commands.scan_cmd import scan_command
from cli.commands.rollback_cmd import rollback_command   
from cli.commands.runtime_cmd import runtime_status_command
from cli.commands.quarantine_cmd import quarantine_status_cmd

@click.group()
@click.version_option(version=__version__, prog_name="guardops")
def cli():
    """
    GuardOps — Autonomous DevSecOps CLI.

    Builds, scans, and deploys your application with security gates
    at every stage of the pipeline.

    \b
    Quick start:
      guardops init            Set up a new project
      guardops deploy          Build, scan, and deploy
      guardops deploy --env prod   Deploy to production (EKS + ECR)
      guardops status          Check pod health
      guardops scan            Run security scans only
      guardops rollback        Roll back to a previous revision
      guardops logs            Stream pod logs
    """
    pass


cli.add_command(init_command,     name="init")
cli.add_command(deploy_command,   name="deploy")
cli.add_command(status_command,   name="status")
cli.add_command(logs_command,     name="logs")
cli.add_command(scan_command,     name="scan")
cli.add_command(rollback_command, name="rollback")   
cli.add_command(runtime_status_command, name="runtime-status")
cli.add_command(quarantine_status_cmd)


