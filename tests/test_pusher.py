"""
tests/test_pusher.py — backend/pipeline/pusher.

The ECR push orchestration (every failure branch + the happy path) and the boto3
helpers (account id, ECR repo ensure/create, docker auth). boto3, docker, and the
ECR helpers are mocked — nothing touches AWS or Docker.
"""

import base64
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from backend.pipeline import pusher
from backend.pipeline.pusher import push_to_ecr


def _proc(returncode=0, stderr=""):
    return MagicMock(returncode=returncode, stderr=stderr)


_REGISTRY_CFG = {"docker": {"registry": "123456789012.dkr.ecr.ap-south-1.amazonaws.com"}}


# ── push_to_ecr orchestration ─────────────────────────────────────────────────

def test_push_success():
    with patch("backend.pipeline.pusher._ensure_ecr_repository", return_value=True), \
         patch("backend.pipeline.pusher._authenticate_docker_to_ecr", return_value=True), \
         patch("backend.pipeline.pusher._tag_and_push_latest", return_value=True), \
         patch("backend.pipeline.pusher.run_command", return_value=_proc()):
        res = push_to_ecr("test-app:abc123", _REGISTRY_CFG)
    assert res.success
    assert res.repository == "test-app" and res.tag == "abc123"
    assert res.image_uri.endswith("/test-app:abc123")


def test_push_no_account_id(monkeypatch):
    monkeypatch.delenv("AWS_ACCOUNT_ID", raising=False)
    monkeypatch.setattr("backend.pipeline.pusher._get_account_id", lambda: "")
    res = push_to_ecr("test-app:abc", {})   # no registry, no account id
    assert not res.success and "account ID" in res.error_message


def test_push_ecr_repo_failure(monkeypatch):
    monkeypatch.setenv("AWS_ACCOUNT_ID", "123456789012")
    with patch("backend.pipeline.pusher._ensure_ecr_repository", return_value=False):
        res = push_to_ecr("test-app:abc", {})
    assert not res.success and "ECR repository" in res.error_message


def test_push_auth_failure(monkeypatch):
    monkeypatch.setenv("AWS_ACCOUNT_ID", "123456789012")
    with patch("backend.pipeline.pusher._ensure_ecr_repository", return_value=True), \
         patch("backend.pipeline.pusher._authenticate_docker_to_ecr", return_value=False):
        res = push_to_ecr("test-app:abc", {})
    assert not res.success and "authentication failed" in res.error_message


def test_push_docker_tag_failure(monkeypatch):
    monkeypatch.setenv("AWS_ACCOUNT_ID", "123456789012")
    with patch("backend.pipeline.pusher._ensure_ecr_repository", return_value=True), \
         patch("backend.pipeline.pusher._authenticate_docker_to_ecr", return_value=True), \
         patch("backend.pipeline.pusher.run_command", return_value=_proc(returncode=1, stderr="tag err")):
        res = push_to_ecr("test-app:abc", {})
    assert not res.success and "docker tag failed" in res.error_message


def test_push_docker_push_failure(monkeypatch):
    monkeypatch.setenv("AWS_ACCOUNT_ID", "123456789012")
    with patch("backend.pipeline.pusher._ensure_ecr_repository", return_value=True), \
         patch("backend.pipeline.pusher._authenticate_docker_to_ecr", return_value=True), \
         patch("backend.pipeline.pusher.run_command", side_effect=[_proc(), _proc(returncode=1)]):
        res = push_to_ecr("test-app:abc", {})
    assert not res.success and "docker push failed" in res.error_message


# ── helpers ───────────────────────────────────────────────────────────────────

def test_get_account_id_success(monkeypatch):
    sts = MagicMock()
    sts.get_caller_identity.return_value = {"Account": "123456789012"}
    monkeypatch.setattr("backend.pipeline.pusher.boto3.client", lambda *a, **k: sts)
    assert pusher._get_account_id() == "123456789012"


def test_get_account_id_error(monkeypatch):
    def boom(*a, **k):
        raise Exception("no creds")
    monkeypatch.setattr("backend.pipeline.pusher.boto3.client", boom)
    assert pusher._get_account_id() == ""


def test_ensure_ecr_repo_already_exists(monkeypatch):
    ecr = MagicMock()
    ecr.describe_repositories.return_value = {"repositories": [{}]}
    monkeypatch.setattr("backend.pipeline.pusher.boto3.client", lambda *a, **k: ecr)
    assert pusher._ensure_ecr_repository("test-app", "ap-south-1") is True


def test_ensure_ecr_repo_creates_when_missing(monkeypatch):
    class _RNF(Exception):
        pass

    ecr = MagicMock()
    ecr.exceptions.RepositoryNotFoundException = _RNF
    ecr.describe_repositories.side_effect = _RNF()
    monkeypatch.setattr("backend.pipeline.pusher.boto3.client", lambda *a, **k: ecr)
    assert pusher._ensure_ecr_repository("test-app", "ap-south-1") is True
    ecr.create_repository.assert_called_once()


def test_ensure_ecr_repo_error_returns_false(monkeypatch):
    class _RNF(Exception):
        pass

    ecr = MagicMock()
    ecr.exceptions.RepositoryNotFoundException = _RNF
    ecr.describe_repositories.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "DescribeRepositories")
    monkeypatch.setattr("backend.pipeline.pusher.boto3.client", lambda *a, **k: ecr)
    assert pusher._ensure_ecr_repository("test-app", "ap-south-1") is False


def test_authenticate_docker_success(monkeypatch):
    ecr = MagicMock()
    token = base64.b64encode(b"AWS:secret").decode()
    ecr.get_authorization_token.return_value = {"authorizationData": [{"authorizationToken": token}]}
    monkeypatch.setattr("backend.pipeline.pusher.boto3.client", lambda *a, **k: ecr)
    monkeypatch.setattr("backend.pipeline.pusher.subprocess.run", lambda *a, **k: MagicMock(returncode=0))
    assert pusher._authenticate_docker_to_ecr("reg", "ap-south-1") is True


def test_authenticate_docker_error(monkeypatch):
    def boom(*a, **k):
        raise Exception("token error")
    monkeypatch.setattr("backend.pipeline.pusher.boto3.client", boom)
    assert pusher._authenticate_docker_to_ecr("reg", "ap-south-1") is False
