"""
backend/pipeline/builder.py

Handles building Docker images.

WHY in backend/ not cli/:
  The CLI (cli/) handles USER INTERACTION — prompts, colors, progress bars.
  The backend/ handles BUSINESS LOGIC — actually building things.
  This separation lets us:
    1. Call builder.py from the CLI command
    2. Call builder.py from a future REST API
    3. Call builder.py from tests without any CLI setup
    4. Test the build logic without running Click

Each function returns a BuildResult dataclass instead of printing or exiting.
The CALLER (the CLI command) decides how to handle failures.
This is called "separation of concerns."
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from cli.utils.system import run_command
from cli.utils.output import info, console


@dataclass
class BuildResult:
    """
    Represents the result of a Docker build operation.

    Using a dataclass instead of a raw dict gives us:
    - Type checking (mypy can catch mistakes)
    - Auto-generated __repr__ for debugging
    - Clear documentation of what fields exist

    @dataclass automatically generates __init__, __repr__, __eq__
    based on the field definitions below.
    """
    success: bool
    image_name: str           # e.g., "my-api"
    image_tag: str            # e.g., "abc123" or "latest"
    full_image_ref: str       # e.g., "my-api:abc123"
    build_duration_seconds: float = 0.0
    error_message: str = ""
    warnings: list[str] = field(default_factory=list)
    # field(default_factory=list) is necessary for mutable defaults.
    # You can't write: warnings: list = [] in a dataclass —
    # that would share the SAME list object across all instances (a classic Python bug).


def build_image(
    project_name: str,
    dockerfile_path: str,
    build_context: str,
    image_tag: str = "latest",
    registry: str = "",
    build_args: Optional[dict[str, str]] = None,
) -> BuildResult:
    """
    Builds a Docker image and optionally tags it for a registry.

    Args:
        project_name:    Name of the project (used as image name base)
        dockerfile_path: Path to the Dockerfile (relative or absolute)
        build_context:   Directory Docker uses as the build context
                         (where it looks for files COPY'd into the image)
        image_tag:       Version tag for the image (git SHA in CI, "latest" locally)
        registry:        Registry prefix (e.g., "123.dkr.ecr.us-east-1.amazonaws.com")
                         Empty string means local-only image
        build_args:      Optional dict of Docker build arguments
                         e.g., {"NODE_ENV": "production", "API_URL": "..."}

    Returns:
        BuildResult with success/failure info.
    """
    import time

    # Sanitize the image name: Docker requires lowercase, no spaces
    safe_name = _sanitize_image_name(project_name)
    full_tag = f"{safe_name}:{image_tag}"

    # If a registry is specified, prefix the image name
    # e.g., "123.ecr.amazonaws.com/my-api:abc123"
    if registry:
        full_tag = f"{registry}/{full_tag}"

    # Build the docker command as a list
    # NEVER build as a string like f"docker build -t {full_tag} {build_context}"
    # because spaces or special chars in paths would break the command.
    cmd = [
        "docker", "build",
        "-t", full_tag,
        "-f", dockerfile_path,
    ]

    # Add build arguments if any were provided
    if build_args:
        for key, value in build_args.items():
            # Each --build-arg needs to be its own list element
            cmd.extend(["--build-arg", f"{key}={value}"])

    # Add the build context (usually ".") as the last argument
    cmd.append(build_context)

    info(f"Building image [cyan]{full_tag}[/cyan]")
    console.print(f"[dim]  Dockerfile: {dockerfile_path}[/dim]")
    console.print(f"[dim]  Context:    {build_context}[/dim]")

    start_time = time.time()

    # capture_output=False so Docker's build output streams live to the terminal
    # The user can see each layer being built in real time
    result = run_command(cmd, capture_output=False, show_command=True)

    duration = time.time() - start_time

    if result.returncode != 0:
        return BuildResult(
            success=False,
            image_name=safe_name,
            image_tag=image_tag,
            full_image_ref=full_tag,
            build_duration_seconds=duration,
            error_message=f"docker build exited with code {result.returncode}",
        )

    return BuildResult(
        success=True,
        image_name=safe_name,
        image_tag=image_tag,
        full_image_ref=full_tag,
        build_duration_seconds=duration,
    )


def image_exists_locally(image_ref: str) -> bool:
    """
    Checks if a Docker image already exists in the local image cache.
    Used to skip rebuilding when deploying a just-built image.

    `docker image inspect` exits 0 if the image exists, 1 if not.
    We capture output to suppress Docker's JSON output to stdout.
    """
    result = run_command(
        ["docker", "image", "inspect", image_ref],
        capture_output=True
    )
    return result.returncode == 0


def get_image_size(image_ref: str) -> str:
    """
    Returns the human-readable size of a Docker image.
    Used for informational output after a build.

    docker image inspect --format '{{.Size}}' returns size in bytes.
    We use a Go template format string to extract just the size field.

    Returns: Size string like "142 MB" or "" if image not found.
    """
    result = run_command(
        ["docker", "image", "inspect", "--format", "{{.Size}}", image_ref],
        capture_output=True
    )
    if result.returncode != 0:
        return ""

    # Convert bytes (integer string) to human-readable
    try:
        size_bytes = int(result.stdout.strip())
        if size_bytes > 1_000_000_000:
            return f"{size_bytes / 1_000_000_000:.1f} GB"
        elif size_bytes > 1_000_000:
            return f"{size_bytes / 1_000_000:.0f} MB"
        else:
            return f"{size_bytes / 1_000:.0f} KB"
    except ValueError:
        return result.stdout.strip()


def _sanitize_image_name(name: str) -> str:
    """
    Docker image names must be:
    - Lowercase
    - Only contain alphanumeric characters, hyphens, and underscores
    - Not start or end with a hyphen

    This function makes any project name Docker-safe.
    """
    sanitized = name.lower()
    # Replace anything that isn't a-z, 0-9, or hyphen with a hyphen
    sanitized = re.sub(r'[^a-z0-9-]', '-', sanitized)
    # Remove leading/trailing hyphens
    sanitized = sanitized.strip('-')
    return sanitized or "guardops-app"