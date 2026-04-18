"""
tests/test_builder.py

Tests for Docker build logic.

Uses pytest-mock to mock subprocess calls so tests run WITHOUT
actually invoking Docker. This makes tests:
  - Fast (no network/IO)
  - Reliable (not affected by Docker installation)
  - Isolated (each test controls exactly what subprocess returns)
"""

import pytest
from unittest.mock import MagicMock, patch
from backend.pipeline.builder import build_image, _sanitize_image_name, BuildResult


def test_sanitize_image_name_lowercase():
    assert _sanitize_image_name("MyApp") == "myapp"


def test_sanitize_image_name_replaces_spaces():
    assert _sanitize_image_name("my app") == "my-app"


def test_sanitize_image_name_replaces_underscores():
    assert _sanitize_image_name("my_app") == "my-app"


def test_sanitize_image_name_handles_empty():
    result = _sanitize_image_name("")
    assert result == "guardops-app"  # Falls back to default


def test_build_image_success(mocker):
    """
    Test that build_image() returns a successful BuildResult
    when docker build exits 0.

    mocker.patch replaces the real run_command with a fake version.
    The fake returns a mock with returncode=0.
    """
    mock_result = MagicMock()
    mock_result.returncode = 0

    # Patch the run_command function as imported in builder.py
    mocker.patch("backend.pipeline.builder.run_command", return_value=mock_result)

    result = build_image(
        project_name="test-app",
        dockerfile_path="Dockerfile",
        build_context=".",
        image_tag="abc123",
    )

    assert result.success is True
    assert result.image_name == "test-app"
    assert result.image_tag == "abc123"
    assert result.full_image_ref == "test-app:abc123"
    assert result.error_message == ""


def test_build_image_failure(mocker):
    """
    Test that build_image() returns a failed BuildResult
    when docker build exits non-zero.
    """
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "ERROR: Dockerfile not found"

    mocker.patch("backend.pipeline.builder.run_command", return_value=mock_result)

    result = build_image(
        project_name="test-app",
        dockerfile_path="Dockerfile",
        build_context=".",
    )

    assert result.success is False
    assert "exited with code 1" in result.error_message


def test_build_image_with_registry(mocker):
    """Test that registry prefix is correctly prepended to image ref."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mocker.patch("backend.pipeline.builder.run_command", return_value=mock_result)

    result = build_image(
        project_name="my-api",
        dockerfile_path="Dockerfile",
        build_context=".",
        image_tag="v1.2.3",
        registry="123.dkr.ecr.us-east-1.amazonaws.com",
    )

    assert result.success is True
    assert result.full_image_ref == "123.dkr.ecr.us-east-1.amazonaws.com/my-api:v1.2.3"