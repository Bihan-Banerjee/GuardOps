import base64
import json
import os
import subprocess
import shutil
from dataclasses import dataclass
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from cli.utils.output import info, warn
from cli.utils.system import run_command


@dataclass
class PushResult:
    success: bool
    image_uri: str
    registry: str
    repository: str
    tag: str
    error_message: str = ""


def push_to_ecr(
    image_name: str,
    image_tag: str,
    config: dict,
    region: Optional[str] = None,
    account_id: Optional[str] = None,
) -> PushResult:
    region = region or os.environ.get("AWS_REGION", "us-east-1")
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
                    "Set AWS_ACCOUNT_ID in .env or configure AWS CLI credentials."
                ),
            )

    registry = f"{account_id}.dkr.ecr.{region}.amazonaws.com"
    repository = image_name
    remote_image_uri = f"{registry}/{repository}:{image_tag}"
    local_image_ref = f"{image_name}:{image_tag}"

    ecr_created = _ensure_ecr_repository(repository, region)
    if not ecr_created:
        return PushResult(
            success=False,
            image_uri=remote_image_uri,
            registry=registry,
            repository=repository,
            tag=image_tag,
            error_message=f"Could not create or verify ECR repository: {repository}",
        )

    auth_ok = _authenticate_docker_to_ecr(registry, region)
    if not auth_ok:
        return PushResult(
            success=False,
            image_uri=remote_image_uri,
            registry=registry,
            repository=repository,
            tag=image_tag,
            error_message="Docker ECR authentication failed. Check AWS credentials.",
        )

    tag_result = run_command(
        ["docker", "tag", local_image_ref, remote_image_uri],
        capture_output=True,
    )
    if tag_result.returncode != 0:
        return PushResult(
            success=False,
            image_uri=remote_image_uri,
            registry=registry,
            repository=repository,
            tag=image_tag,
            error_message=f"docker tag failed: {tag_result.stderr.strip()}",
        )

    info(f"Pushing [cyan]{remote_image_uri}[/cyan] to ECR...")
    push_result = run_command(
        ["docker", "push", remote_image_uri],
        capture_output=False,
    )
    if push_result.returncode != 0:
        return PushResult(
            success=False,
            image_uri=remote_image_uri,
            registry=registry,
            repository=repository,
            tag=image_tag,
            error_message="docker push failed",
        )

    also_tag_latest = _tag_and_push_latest(
        local_image_ref, registry, repository, region
    )

    return PushResult(
        success=True,
        image_uri=remote_image_uri,
        registry=registry,
        repository=repository,
        tag=image_tag,
    )


def _get_account_id() -> str:
    try:
        sts = boto3.client("sts")
        return sts.get_caller_identity()["Account"]
    except Exception:
        return ""


def _ensure_ecr_repository(repository: str, region: str) -> bool:
    try:
        ecr = boto3.client("ecr", region_name=region)
        try:
            ecr.describe_repositories(repositoryNames=[repository])
            return True
        except ecr.exceptions.RepositoryNotFoundException:
            ecr.create_repository(
                repositoryName=repository,
                imageScanningConfiguration={"scanOnPush": True},
                encryptionConfiguration={"encryptionType": "AES256"},
            )
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
        )
        return result.returncode == 0
    except Exception as e:
        warn(f"ECR auth error: {e}")
        return False


def _tag_and_push_latest(
    local_ref: str,
    registry: str,
    repository: str,
    region: str,
) -> bool:
    latest_uri = f"{registry}/{repository}:latest"
    tag_r = run_command(["docker", "tag", local_ref, latest_uri], capture_output=True)
    if tag_r.returncode != 0:
        return False
    push_r = run_command(["docker", "push", latest_uri], capture_output=True)
    return push_r.returncode == 0