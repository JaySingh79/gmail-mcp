"""Tests for discovering OAuth token and credentials from any directory, user home, and PATH."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from gmail_mcp.auth.token_manager import TokenManager
from gmail_mcp.config import (
    GmailConfig,
    get_candidate_search_directories,
    resolve_credentials_path,
    resolve_token_path,
)


def test_token_and_credentials_discovery_from_arbitrary_cwd(tmp_path: Path) -> None:
    """When executed from any directory on the machine, GmailConfig finds project tokens and credentials."""
    orig_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        config = GmailConfig()
        assert config.token_path.exists()
        assert config.token_path.name == "token.json"
        assert config.credentials_path.exists()
        assert config.credentials_path.name in ("creds_mcp_agy.json", "creds_mcp_agy copy.json", "credentials.json")

        mgr = TokenManager(config)
        assert mgr.token_path.exists()
        assert mgr.credentials_path.exists()
    finally:
        os.chdir(orig_cwd)


def test_token_discovery_from_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When a token.json is placed in the current working directory, it is resolved."""
    monkeypatch.delenv("GMAIL_TOKEN_PATH", raising=False)
    orig_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        local_token = tmp_path / "token.json"
        local_token.write_text('{"type": "local_cwd"}', encoding="utf-8")

        found = resolve_token_path()
        assert found == local_token.resolve()
    finally:
        os.chdir(orig_cwd)


def test_credentials_discovery_from_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When a credentials.json is placed in the current working directory, it is resolved."""
    monkeypatch.delenv("GMAIL_CREDENTIALS_PATH", raising=False)
    orig_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        local_creds = tmp_path / "credentials.json"
        local_creds.write_text('{"installed": {}}', encoding="utf-8")

        found = resolve_credentials_path()
        assert found == local_creds.resolve()
    finally:
        os.chdir(orig_cwd)


def test_token_discovery_from_system_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When a directory in PATH contains token.json, resolve_token_path discovers it."""
    path_dir = tmp_path / "in_path_bin"
    path_dir.mkdir(parents=True, exist_ok=True)
    path_token = path_dir / "gmail_token.json"
    path_token.write_text('{"type": "in_path"}', encoding="utf-8")

    monkeypatch.setenv("PATH", f"{path_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.delenv("GMAIL_TOKEN_PATH", raising=False)

    with patch("gmail_mcp.config.TOKEN_CANDIDATE_FILENAMES", ["gmail_token.json"]):
        found = resolve_token_path()
        assert found == path_token.resolve()


def test_credentials_discovery_from_system_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When a directory in PATH contains client_secrets.json, resolve_credentials_path discovers it."""
    path_dir = tmp_path / "in_path_tools"
    path_dir.mkdir(parents=True, exist_ok=True)
    path_creds = path_dir / "client_secrets.json"
    path_creds.write_text('{"installed": {"client_id": "test"}}', encoding="utf-8")

    monkeypatch.setenv("PATH", f"{path_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.delenv("GMAIL_CREDENTIALS_PATH", raising=False)

    with patch("gmail_mcp.config.CREDENTIALS_CANDIDATE_FILENAMES", ["client_secrets.json"]):
        found = resolve_credentials_path()
        assert found == path_creds.resolve()


def test_explicit_env_vars_take_highest_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit GMAIL_TOKEN_PATH and GMAIL_CREDENTIALS_PATH take precedence over candidate paths."""
    custom_token = tmp_path / "custom_token.json"
    custom_token.write_text("{}", encoding="utf-8")
    custom_creds = tmp_path / "custom_creds.json"
    custom_creds.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("GMAIL_TOKEN_PATH", str(custom_token))
    monkeypatch.setenv("GMAIL_CREDENTIALS_PATH", str(custom_creds))

    config = GmailConfig()
    assert config.token_path == custom_token.resolve()
    assert config.credentials_path == custom_creds.resolve()
