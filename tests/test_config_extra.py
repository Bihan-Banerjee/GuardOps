"""
tests/test_config_extra.py — cli/utils/config (resolve/merge helpers).

Environment-aware config merging, namespace/tag/release-name/domain resolution, the
metadata flag, and the empty-config guard.
"""

from pathlib import Path

import pytest

from cli.utils.config import (
    is_metadata_enabled, merge_with_defaults, get_env_config, resolve_namespace,
    resolve_image_tag, resolve_helm_release_name, resolve_domain, load_config,
)


def test_is_metadata_enabled():
    assert is_metadata_enabled({}) is True
    assert is_metadata_enabled({"metadata": {"enabled": False}}) is False


def test_merge_with_defaults_adds_new_top_level_key():
    merged = merge_with_defaults({"brandnew": "v", "project": {"name": "x"}})
    assert merged["brandnew"] == "v"
    assert merged["project"]["name"] == "x"


def test_get_env_config_overrides_and_preserves():
    config = {"kubernetes": {"namespace": "default", "keep": 1},
              "environments": {"staging": {"kubernetes": {"namespace": "staging"}}}}
    merged = get_env_config(config, "staging")
    assert merged["kubernetes"]["namespace"] == "staging"   # overridden
    assert merged["kubernetes"]["keep"] == 1                # base preserved


def test_get_env_config_nested_section_merge():
    config = {"security": {"tools": {"semgrep": True}},
              "environments": {"prod": {"security": {"tools": {"owasp_zap": True}}}}}
    merged = get_env_config(config, "prod")
    assert merged["security"]["tools"]["semgrep"] is True
    assert merged["security"]["tools"]["owasp_zap"] is True


def test_resolve_namespace():
    config = {"kubernetes": {"namespace": "default"},
              "environments": {"staging": {"kubernetes": {"namespace": "staging"}}}}
    assert resolve_namespace(config, "staging") == "staging"
    assert resolve_namespace(config, None) == "default"
    assert resolve_namespace({}, None) == "default"


def test_resolve_image_tag_prefix():
    config = {"environments": {"staging": {"docker": {"image_tag_prefix": "staging"}}}}
    assert resolve_image_tag("abc123", "staging", config) == "staging-abc123"
    assert resolve_image_tag("abc123", "local", config) == "abc123"


def test_resolve_helm_release_name_slot_and_sanitize():
    config = {"environments": {"staging": {"helm": {"release_suffix": "-staging"}}}}
    assert resolve_helm_release_name("My_App", "staging", config, slot="blue") == "my-app-staging-blue"


def test_resolve_helm_release_name_truncates_to_53():
    out = resolve_helm_release_name("a" * 60, "local", {})
    assert len(out) <= 53


def test_resolve_domain_user_override():
    assert resolve_domain({"environments": {"prod": {"domain": "myapp.com"}}}, "prod") == "myapp.com"
    assert isinstance(resolve_domain({}, "prod"), str)   # default fallback path


def test_load_config_empty_file_exits(runner):
    with runner.isolated_filesystem():
        Path(".guardops.yaml").write_text("", encoding="utf-8")
        with pytest.raises(SystemExit):
            load_config()
