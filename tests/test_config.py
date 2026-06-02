"""
tests/test_config.py

Tests for the config module.
Demonstrates how to write unit tests for GuardOps.

pytest conventions:
  - Files named test_*.py are discovered automatically
  - Functions named test_* are run as tests
  - Use assert statements (pytest rewrites them for better output)
  - Use tmp_path fixture for temporary directories (auto-cleaned up)
"""

import pytest
import yaml
from pathlib import Path

# We change directory in tests so config.py finds files in the right place.
# monkeypatch is a pytest fixture for temporarily changing things.
from cli.utils.config import (
    save_config,
    load_config,
    config_exists,
    merge_with_defaults,
    DEFAULT_CONFIG,
    CONFIG_FILENAME,
)


def test_config_not_found_exits(tmp_path, monkeypatch):
    """load_config() should exit with code 1 when .guardops.yaml doesn't exist."""
    # Change working directory to a fresh temp dir (no .guardops.yaml there)
    monkeypatch.chdir(tmp_path)

    # pytest.raises catches the SystemExit and lets us assert on it
    with pytest.raises(SystemExit) as exc_info:
        load_config()

    assert exc_info.value.code == 1  # Should exit with code 1, not 0


def test_load_config_strips_utf8_bom(tmp_path, monkeypatch):
    """A BOM-prefixed .guardops.yaml must still parse the first key correctly.

    Some Windows editors save UTF-8 with a BOM; without utf-8-sig the leading
    bytes would turn "project" into "﻿project" and break get_project_name.
    """
    monkeypatch.chdir(tmp_path)
    # encoding="utf-8-sig" writes the BOM in front of the content.
    (tmp_path / CONFIG_FILENAME).write_text(
        "project:\n  name: bom-demo\n", encoding="utf-8-sig"
    )
    config = load_config()
    assert config["project"]["name"] == "bom-demo"


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    """Saving a config and loading it back should produce identical data."""
    monkeypatch.chdir(tmp_path)

    original = {
        "project": {"name": "test-project", "cloud": "local"},
        "kubernetes": {"namespace": "test-ns"},
    }

    save_config(original)

    # Verify the file was actually created
    assert (tmp_path / CONFIG_FILENAME).exists()

    loaded = load_config()

    # Each key we set should be present and correct
    assert loaded["project"]["name"] == "test-project"
    assert loaded["project"]["cloud"] == "local"
    assert loaded["kubernetes"]["namespace"] == "test-ns"


def test_config_exists_false_when_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert config_exists() is False


def test_config_exists_true_after_save(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_config({"project": {"name": "test"}})
    assert config_exists() is True


def test_merge_with_defaults_fills_missing_keys():
    """merge_with_defaults should add missing keys from DEFAULT_CONFIG."""
    partial = {"project": {"name": "my-app"}}
    merged = merge_with_defaults(partial)

    # Keys from partial should be preserved
    assert merged["project"]["name"] == "my-app"

    # Keys from DEFAULT_CONFIG that weren't in partial should be filled
    assert "docker" in merged
    assert "kubernetes" in merged
    assert "security" in merged


def test_invalid_yaml_exits(tmp_path, monkeypatch):
    """load_config() should exit on malformed YAML."""
    monkeypatch.chdir(tmp_path)

    # Write deliberately broken YAML
    (tmp_path / CONFIG_FILENAME).write_text("key: [\ninvalid yaml {{{")

    with pytest.raises(SystemExit):
        load_config()