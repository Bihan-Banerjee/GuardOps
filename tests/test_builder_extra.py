"""
tests/test_builder_extra.py — backend/pipeline/builder (uncovered branches).

Build-args, registry prefix, the image existence + size helpers, and the name
sanitiser. run_command is mocked — Docker is never invoked.
"""

from unittest.mock import MagicMock, patch

from backend.pipeline.builder import (
    build_image, image_exists_locally, get_image_size, _sanitize_image_name,
)

_RC = "backend.pipeline.builder.run_command"


def test_build_success_with_build_args():
    with patch(_RC, return_value=MagicMock(returncode=0)) as mk:
        r = build_image("My App", "Dockerfile", ".", image_tag="abc", build_args={"K": "V"})
    assert r.success and r.full_image_ref == "my-app:abc"
    cmd = mk.call_args[0][0]
    assert "--build-arg" in cmd and "K=V" in cmd


def test_build_with_registry_prefix():
    with patch(_RC, return_value=MagicMock(returncode=0)):
        r = build_image("app", "Dockerfile", ".", image_tag="t", registry="reg.io")
    assert r.full_image_ref == "reg.io/app:t"


def test_build_failure():
    with patch(_RC, return_value=MagicMock(returncode=1)):
        r = build_image("app", "Dockerfile", ".")
    assert not r.success and "exited with code 1" in r.error_message


def test_image_exists_locally():
    with patch(_RC, return_value=MagicMock(returncode=0)):
        assert image_exists_locally("app:t") is True
    with patch(_RC, return_value=MagicMock(returncode=1)):
        assert image_exists_locally("app:t") is False


def test_get_image_size_units():
    with patch(_RC, return_value=MagicMock(returncode=0, stdout="142000000")):
        assert get_image_size("app:t") == "142 MB"
    with patch(_RC, return_value=MagicMock(returncode=0, stdout="2000000000")):
        assert get_image_size("app:t") == "2.0 GB"
    with patch(_RC, return_value=MagicMock(returncode=0, stdout="5000")):
        assert get_image_size("app:t") == "5 KB"


def test_get_image_size_not_found_and_unparseable():
    with patch(_RC, return_value=MagicMock(returncode=1)):
        assert get_image_size("app:t") == ""
    with patch(_RC, return_value=MagicMock(returncode=0, stdout="notanumber")):
        assert get_image_size("app:t") == "notanumber"


def test_sanitize_image_name():
    assert _sanitize_image_name("My App!") == "my-app"
    assert _sanitize_image_name("---") == "guardops-app"
