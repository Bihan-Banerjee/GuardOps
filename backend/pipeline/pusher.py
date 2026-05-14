"""
backend/pipeline/pusher.py

Pushes a locally-built Docker image to AWS ECR.

Called by deploy_cmd.py as:
    push_result = push_to_ecr(full_image_ref, config)

Where full_image_ref is "image-name:tag" (e.g. "test-app:abc1234").
The ECR registry URL comes from:
    1. config["docker"]["registry"]  (set in .guardops.yaml)
    2. AWS_ACCOUNT_ID env var + AWS_REGION env var  (fallback)
    3. boto3 STS call  (last resort)
"""

import base64
import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from cli.utils.output import info, warn
from cli.utils.system import run_command


@dataclass
class PushResult:
    success: bool
    image_uri: str          # Full remote URI that was pushed, e.g. 123.dkr.ecr.ap-south-1.amazonaws.com/test-app:abc1234
    registry: str           # Registry base URL, e.g. 123.dkr.ecr.ap-south-1.amazonaws.com
    repository: str         # Repository name only, e.g. test-app
    tag: str                # Image tag, e.g. abc1234
    error_message: str = ""


def push_to_ecr(
    image_ref: str,
    config: dict,
    region: Optional[str] = None,
    account_id: Optional[str] = None,
) -> PushResult:
    """
    Push a local Docker image to ECR.

    Args:
        image_ref:  Full local image reference in "name:tag" format.
                    E.g. "test-app:abc1234"
        config:     Parsed .guardops.yaml dict. Uses config["docker"]["registry"]
                    if set, otherwise resolves registry from AWS credentials.
        region:     AWS region override. Defaults to AWS_REGION env var,
                    then falls back to "ap-south-1".
        account_id: AWS account ID override. Defaults to AWS_ACCOUNT_ID env var,
                    then calls STS to resolve automatically.

    Returns:
        PushResult with success=True and image_uri set to the remote ECR URI.
    """

    # ── Resolve image name and tag from the full ref ──────────────────────────
    # Split on the LAST colon so ECR URLs with port numbers (e.g. host:5000/name:tag)
    # are handled correctly. For a plain "name:tag" this is equivalent to split(":", 1).
    if ":" in image_ref:
        image_name, image_tag = image_ref.rsplit(":", 1)
    else:
        image_name = image_ref
        image_tag = "latest"

    # ── Resolve AWS region ────────────────────────────────────────────────────
    # Priority: function arg → env var → ap-south-1 (Mumbai)
    # ap-south-1 is closest to India and generally cheapest for Indian users.
    region = region or os.environ.get("AWS_REGION", "ap-south-1")

    # ── Resolve AWS account ID ────────────────────────────────────────────────
    account_id = account_id or os.environ.get("AWS_ACCOUNT_ID", "")
    if not account_id:
        account_id = _get_account_id()
        if not account_id:
            return PushResult(
                success=False,
                image_uri="",
                registry="",
                repository=image_name,
                tag=image_tag,
                error_message=(
                    "Could not determine AWS account ID. "
                    "Either set AWS_ACCOUNT_ID in your environment, "
                    "or ensure your AWS CLI credentials are configured "
                    "(`aws sts get-caller-identity` should return your account ID)."
                ),
            )

    # ── Resolve ECR registry URL ──────────────────────────────────────────────
    # Use the registry from .guardops.yaml if set (prod workflow).
    # Fall back to constructing it from account ID + region.
    registry = (
        config.get("docker", {}).get("registry", "").strip()
        or f"{account_id}.dkr.ecr.{region}.amazonaws.com"
    )

    remote_image_uri = f"{registry}/{image_name}:{image_tag}"
    local_image_ref  = f"{image_name}:{image_tag}"

    # ── Ensure ECR repository exists ──────────────────────────────────────────
    ecr_created = _ensure_ecr_repository(image_name, region)
    if not ecr_created:
        return PushResult(
            success=False,
            image_uri=remote_image_uri,
            registry=registry,
            repository=image_name,
            tag=image_tag,
            error_message=f"Could not create or verify ECR repository: {image_name}",
        )

    # ── Authenticate Docker to ECR ────────────────────────────────────────────
    auth_ok = _authenticate_docker_to_ecr(registry, region)
    if not auth_ok:
        return PushResult(
            success=False,
            image_uri=remote_image_uri,
            registry=registry,
            repository=image_name,
            tag=image_tag,
            error_message=(
                "Docker ECR authentication failed. "
                "Check that your AWS credentials are valid and have ecr:GetAuthorizationToken permission."
            ),
        )

    # ── Tag local image with the remote ECR URI ───────────────────────────────
    tag_result = run_command(
        ["docker", "tag", local_image_ref, remote_image_uri],
        capture_output=True,
    )
    if tag_result.returncode != 0:
        return PushResult(
            success=False,
            image_uri=remote_image_uri,
            registry=registry,
            repository=image_name,
            tag=image_tag,
            error_message=f"docker tag failed: {tag_result.stderr.strip()}",
        )

    # ── Push to ECR ───────────────────────────────────────────────────────────
    info(f"Pushing [cyan]{remote_image_uri}[/cyan] to ECR...")
    push_result = run_command(
        ["docker", "push", remote_image_uri],
        capture_output=False,   # stream output so user sees progress
    )
    if push_result.returncode != 0:
        return PushResult(
            success=False,
            image_uri=remote_image_uri,
            registry=registry,
            repository=image_name,
            tag=image_tag,
            error_message="docker push failed — check Docker output above for details.",
        )

    # ── Also push :latest tag ─────────────────────────────────────────────────
    # Failure here is non-fatal — the versioned tag already pushed successfully.
    _tag_and_push_latest(local_image_ref, registry, image_name)

    return PushResult(
        success=True,
        image_uri=remote_image_uri,
        registry=registry,
        repository=image_name,
        tag=image_tag,
    )


# ── Private helpers ───────────────────────────────────────────────────────────

def _get_account_id() -> str:
    """Calls AWS STS to determine the current account ID."""
    try:
        sts = boto3.client("sts")
        return sts.get_caller_identity()["Account"]
    except Exception:
        return ""


def _ensure_ecr_repository(repository: str, region: str) -> bool:
    """
    Returns True if the ECR repository exists or was successfully created.
    If the repo already exists, does nothing (idempotent).
    """
    try:
        ecr = boto3.client("ecr", region_name=region)
        try:
            ecr.describe_repositories(repositoryNames=[repository])
            return True     # already exists
        except ecr.exceptions.RepositoryNotFoundException:
            # Create with secure defaults
            ecr.create_repository(
                repositoryName=repository,
                imageScanningConfiguration={"scanOnPush": True},
                encryptionConfiguration={"encryptionType": "AES256"},
            )
            # Lifecycle: keep last 10 images only
            # This prevents storage costs from accumulating if CI runs frequently.
            ecr.put_lifecycle_policy(
                repositoryName=repository,
                lifecyclePolicyText=json.dumps({
                    "rules": [{
                        "rulePriority": 1,
                        "description": "Keep last 10 images",
                        "selection": {
                            "tagStatus": "any",
                            "countType": "imageCountMoreThan",
                            "countNumber": 10,
                        },
                        "action": {"type": "expire"},
                    }]
                }),
            )
            return True
    except (BotoCoreError, ClientError) as e:
        warn(f"ECR repository setup error: {e}")
        return False


def _authenticate_docker_to_ecr(registry: str, region: str) -> bool:
    """
    Logs Docker into ECR using a short-lived token from boto3.
    The token is valid for 12 hours. This must be called before every push
    because tokens expire.
    """
    try:
        ecr = boto3.client("ecr", region_name=region)
        token_response = ecr.get_authorization_token()
        auth_data = token_response["authorizationData"][0]
        token = base64.b64decode(auth_data["authorizationToken"]).decode()
        username, password = token.split(":", 1)

        result = subprocess.run(
            ["docker", "login", "--username", username, "--password-stdin", registry],
            input=password,
            capture_output=True,
            text=True,
            encoding="utf-8",   # explicit UTF-8 — avoids cp1252 errors on Windows
        )
        return result.returncode == 0
    except Exception as e:
        warn(f"ECR auth error: {e}")
        return False


def _tag_and_push_latest(local_ref: str, registry: str, repository: str) -> bool:
    """
    Tags and pushes the :latest tag in addition to the versioned tag.
    This makes it easy to pull the most recent image without knowing the exact tag.
    Non-fatal if it fails — the versioned push already succeeded.
    """
    latest_uri = f"{registry}/{repository}:latest"
    tag_r = run_command(["docker", "tag", local_ref, latest_uri], capture_output=True)
    if tag_r.returncode != 0:
        return False
    push_r = run_command(["docker", "push", latest_uri], capture_output=False)
    return push_r.returncode == 0
