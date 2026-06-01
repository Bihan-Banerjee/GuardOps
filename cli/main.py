"""
cli/main.py — Root Click group. Registers all guardops subcommands.

Phase 9 changes:
  - Imported and registered switch_command (guardops switch)
  - Updated docstring quick-start to include switch and staging examples

Phase 10 changes:
  - Imported and registered sync_status_command (guardops sync-status)
  - Updated docstring quick-start to include sync-status and GitOps examples

Phase 11 changes:
  - Imported and registered verify_image_command (guardops verify-image)
  - Imported and registered sbom_command (guardops sbom)
  - Updated docstring quick-start to include supply-chain examples
"""

import click
from cli import __version__

from cli.commands.init_cmd       import init_command
from cli.commands.deploy_cmd     import deploy_command
from cli.commands.status_cmd     import status_command
from cli.commands.logs_cmd       import logs_command
from cli.commands.scan_cmd       import scan_command
from cli.commands.rollback_cmd   import rollback_command
from cli.commands.runtime_cmd    import runtime_status_command
from cli.commands.quarantine_cmd import quarantine_status_cmd
from cli.commands.switch_cmd     import switch_command           # Phase 9
from cli.commands.sync_cmd       import sync_status_command      # Phase 10
from cli.commands.verify_cmd     import verify_image_command     # Phase 11
from cli.commands.sbom_cmd       import sbom_command             # Phase 11


@click.group()
@click.version_option(version=__version__, prog_name="guardops")
def cli():
    """
    GuardOps — Autonomous DevSecOps CLI.

    Builds, scans, and deploys your application with security gates
    at every stage of the pipeline.

    \b
    Quick start:
      guardops init                              Set up a new project
      guardops deploy                            Build, scan, and deploy (local)
      guardops deploy --env staging              Deploy to staging namespace
      guardops deploy --env prod                 Deploy to production (EKS + ECR)
      guardops deploy --env prod --gitops        GitOps mode: commit override + trigger ArgoCD
      guardops deploy --env staging --slot blue  Deploy blue slot (blue-green)
      guardops switch --slot green               Cut traffic to green slot
      guardops sync-status --env prod            Check ArgoCD sync and health
      guardops sync-status --env prod --wait     Wait until Synced + Healthy (CI gate)
      guardops status                            Check pod health
      guardops scan                              Run security scans only
      guardops rollback                          Roll back to a previous revision
      guardops logs                              Stream pod logs
      guardops runtime-status                    Show Falco runtime alerts
      guardops quarantine-status                 Show quarantined pods
      guardops sbom <image>                      Generate a CycloneDX + SPDX SBOM
      guardops verify-image <ref>                Verify the cosign keyless signature
      guardops verify-image <ref> --attestation  Also verify the SBOM attestation
    """
    pass


cli.add_command(init_command,           name="init")
cli.add_command(deploy_command,         name="deploy")
cli.add_command(status_command,         name="status")
cli.add_command(logs_command,           name="logs")
cli.add_command(scan_command,           name="scan")
cli.add_command(rollback_command,       name="rollback")
cli.add_command(runtime_status_command, name="runtime-status")
cli.add_command(quarantine_status_cmd)
cli.add_command(switch_command,         name="switch")           # Phase 9
cli.add_command(sync_status_command,    name="sync-status")      # Phase 10
cli.add_command(verify_image_command,   name="verify-image")     # Phase 11
cli.add_command(sbom_command,           name="sbom")             # Phase 11
