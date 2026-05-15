"""
cli/utils/system.py

Provides a safe, clean wrapper around subprocess to run shell commands.

WHY NOT use subprocess directly in commands?
  1. Error handling would be duplicated in every command.
  2. Output formatting (showing command output in Rich style) would be inconsistent.
  3. Hard to test — this module can be mocked in tests.

HOW subprocess works:
  subprocess.run() runs a command and waits for it to finish.
  It returns a CompletedProcess with:
    .returncode  → 0 means success, non-zero means failure
    .stdout      → captured output (if capture_output=True)
    .stderr      → captured errors (if capture_output=True)

Phase 4.5 changes:
  - Added encoding="utf-8" to all subprocess.run calls.
    Windows defaults to cp1252 which breaks on any non-ASCII output
    (docker build logs, kubectl pod names, Helm output, etc.).
    Explicit utf-8 matches what every tool in the pipeline produces.
  - Added timeout=300 (5 minutes) to all subprocess.run calls.
    Without a timeout, a hung docker push or stuck helm --wait blocks
    the CLI forever with no feedback. 300s is generous enough for any
    legitimate operation (ECR push, Helm atomic deploy) but short enough
    to surface real hangs quickly.
    Commands that legitimately need more time (e.g. terraform apply)
    can pass timeout= explicitly via run_command(..., timeout=600).
"""

import subprocess
import shutil
import sys
from typing import Optional

from cli.utils.output import console, error


# Default timeout for all subprocess calls.
# Override per-call by passing timeout= to run_command().
_DEFAULT_TIMEOUT = 300


def run_command(
    cmd: list[str],
    *,
    capture_output: bool = False,
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
    show_command: bool = False,
    timeout: int = _DEFAULT_TIMEOUT,
) -> subprocess.CompletedProcess:
    """
    Runs a shell command and returns the result.

    Args:
        cmd:            Command as a list, e.g. ["docker", "build", "-t", "myapp", "."]
                        NEVER pass a string — that requires shell=True which is a
                        security risk (shell injection).
        capture_output: If True, stdout/stderr are captured and returned.
                        If False, output streams directly to the terminal.
        cwd:            Working directory to run the command in.
                        None means current directory.
        env:            Environment variables for the subprocess.
                        None means inherit parent's environment.
        show_command:   If True, prints the command before running it (for debugging).
        timeout:        Seconds before the subprocess is killed and TimeoutExpired
                        is raised. Default: 300s. Pass timeout=0 to disable (not
                        recommended — only for commands that truly have no bound).

    Returns:
        subprocess.CompletedProcess — check .returncode for success/failure.

    Raises:
        SystemExit: If the command is not found (e.g., docker not installed).
        SystemExit: If the command exceeds the timeout.
    """
    if show_command:
        console.print(f"[dim]  $ {' '.join(cmd)}[/dim]")

    try:
        result = subprocess.run(
            cmd,
            capture_output=capture_output,
            text=True,
            encoding="utf-8",   # explicit utf-8: fixes cp1252 issues on Windows
            cwd=cwd,
            env=env,
            timeout=timeout if timeout > 0 else None,
        )
        return result

    except FileNotFoundError:
        # This happens when the first element of cmd (e.g., "docker") isn't installed
        error(
            f"Command [bold]{cmd[0]}[/bold] not found. "
            f"Is it installed and on your PATH?"
        )
        sys.exit(1)

    except subprocess.TimeoutExpired:
        error(
            f"Command [bold]{' '.join(cmd[:2])}[/bold] timed out after {timeout}s.\n"
            f"   If this is expected (large image push, slow deploy), "
            f"retry with a longer timeout."
        )
        sys.exit(1)


def run_command_or_exit(
    cmd: list[str],
    failure_message: str,
    **kwargs,
) -> subprocess.CompletedProcess:
    """
    Runs a command and exits with an error message if it fails.
    Use this when a failure should completely stop execution.

    Args:
        cmd:             Command list (same as run_command).
        failure_message: Human-readable message shown if command fails.
        **kwargs:        Passed directly to run_command (including timeout=).
    """
    result = run_command(cmd, **kwargs)
    if result.returncode != 0:
        error(failure_message)
        if result.stderr:
            console.print(f"[dim red]{result.stderr.strip()}[/dim red]")
        sys.exit(result.returncode)
    return result


def check_dependency(command: str, install_hint: str = "") -> bool:
    """
    Checks whether an external tool is installed by looking for it on PATH.

    shutil.which() is the Python equivalent of `which docker` in bash.
    Returns the full path if found, None if not found.

    Args:
        command:      Command name to check (e.g., "docker", "kubectl", "helm")
        install_hint: Message shown if command is missing (how to install it)

    Returns:
        True if command is available, False if not.
    """
    if shutil.which(command) is None:
        error(
            f"[bold]{command}[/bold] is not installed or not on PATH.\n"
            f"   {install_hint}"
        )
        return False
    return True


def check_all_dependencies(required: list[tuple[str, str]]) -> bool:
    """
    Checks multiple dependencies at once.
    Returns False if ANY are missing (so we report all missing tools at once,
    not one at a time).

    Args:
        required: List of (command, install_hint) tuples.

    Example:
        check_all_dependencies([
            ("docker", "Install Docker Desktop from https://docker.com"),
            ("kubectl", "Install with: brew install kubectl"),
        ])
    """
    all_ok = True
    for command, hint in required:
        if not check_dependency(command, hint):
            all_ok = False
    return all_ok


def get_command_output(cmd: list[str], default: str = "") -> str:
    """
    Runs a command and returns its stdout as a stripped string.
    Returns `default` if the command fails or produces no output.

    Useful for reading info from tools, e.g. getting the current docker context.

    Args:
        cmd:     Command list.
        default: Value returned if command fails.
    """
    result = run_command(cmd, capture_output=True)
    if result.returncode == 0 and result.stdout:
        return result.stdout.strip()
    return default
