"""Secure OAuth 2.0 token storage, automatic refresh, and status inspection."""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from pydantic import BaseModel, Field

from gmail_mcp.auth.credentials import (
    DEFAULT_SCOPES,
    SCOPE_GMAIL_FULL_ACCESS,
    TOOL_SCOPE_REQUIREMENTS,
    build_insufficient_scope_report,
)
from gmail_mcp.config import GmailConfig


class AuthStatusReport(BaseModel):
    """Structured authentication status report (never contains raw tokens or secrets)."""

    authenticated: bool
    token_file_exists: bool
    credentials_file_exists: bool
    token_expired: bool = False
    has_refresh_token: bool = False
    expiry_utc: str | None = None
    granted_scopes: list[str] = Field(default_factory=list)
    missing_default_scopes: list[str] = Field(default_factory=list)
    status_code: str
    message: str
    reauthorization_steps: list[str] = Field(default_factory=list)


class TokenManager:
    """Manages OAuth 2.0 user tokens securely without ever logging token secrets."""

    def __init__(self, config: GmailConfig) -> None:
        self.config = config
        self._credentials: Credentials | None = None

    @property
    def token_path(self) -> Path:
        return self.config.token_path

    @property
    def credentials_path(self) -> Path:
        return self.config.credentials_path

    def load_credentials(self, auto_refresh: bool = True) -> Credentials | None:
        """Load credentials from disk and refresh if expired."""
        if self._credentials is not None and self._credentials.valid:
            return self._credentials

        if not self.token_path.exists():
            return None

        try:
            # Do not pass scopes=... to from_authorized_user_file so it preserves the actual
            # scopes stored in token.json rather than overwriting them with requested defaults.
            creds = Credentials.from_authorized_user_file(str(self.token_path))
        except Exception as exc:
            raise RuntimeError(
                f"Failed to parse OAuth token file at {self.token_path.name}: {type(exc).__name__}"
            ) from exc

        if creds and creds.expired and creds.refresh_token and auto_refresh:
            try:
                creds.refresh(Request())
                self.save_credentials(creds)
            except RefreshError as exc:
                self._credentials = None
                raise PermissionError(
                    "OAuth refresh token has expired or been revoked. Reauthorization is required: "
                    "run `uv run --no-project python -m gmail_mcp.auth.oauth --login --force-reauth`."
                ) from exc

        self._credentials = creds
        return creds

    def save_credentials(self, creds: Credentials) -> None:
        """Persist credentials to token_path with owner-only file permissions where supported."""
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        serialized = creds.to_json()
        self.token_path.write_text(serialized, encoding="utf-8")
        try:
            os.chmod(self.token_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        self._credentials = creds

    def set_credentials(self, creds: Credentials, persist: bool = False) -> None:
        """Inject credentials directly (e.g. for unit tests or in-memory sessions)."""
        self._credentials = creds
        if persist:
            self.save_credentials(creds)

    def clear_cached_credentials(self) -> None:
        """Clear in-memory cached credentials."""
        self._credentials = None

    def get_granted_scopes(self) -> list[str]:
        """Return the list of scopes granted to the current token."""
        creds = self._credentials
        if creds is None and self.token_path.exists():
            try:
                raw = json.loads(self.token_path.read_text(encoding="utf-8"))
                scopes = raw.get("scopes") or []
                if isinstance(scopes, str):
                    scopes = scopes.split()
                return sorted(set(scopes))
            except Exception:
                return []
        if creds and creds.scopes:
            return sorted(set(creds.scopes))
        return []

    def has_scope_for_tool(self, tool_name: str, require_full_access: bool = False) -> tuple[bool, dict[str, Any] | None]:
        """Check if currently granted scopes satisfy the tool's requirement."""
        granted = set(self.get_granted_scopes())
        if not granted:
            # If token file doesn't explicitly list scopes, let the API call validate it
            return True, None

        if SCOPE_GMAIL_FULL_ACCESS in granted:
            return True, None

        if require_full_access:
            return False, build_insufficient_scope_report(
                tool_name, granted, required_scope_override=SCOPE_GMAIL_FULL_ACCESS
            )

        req_scope, _ = TOOL_SCOPE_REQUIREMENTS.get(tool_name, (None, ""))
        if req_scope and req_scope not in granted:
            return False, build_insufficient_scope_report(tool_name, granted)
        return True, None

    def get_auth_status(self, auto_refresh: bool = True) -> AuthStatusReport:
        """Inspect OAuth token state and return a sanitized AuthStatusReport."""
        creds_exists = self.credentials_path.exists()
        token_exists = self.token_path.exists()

        if not token_exists:
            return AuthStatusReport(
                authenticated=False,
                token_file_exists=False,
                credentials_file_exists=creds_exists,
                status_code="TOKEN_MISSING",
                message=(
                    f"OAuth token file ({self.token_path.name}) does not exist. "
                    "Complete the installed-application OAuth flow to authenticate."
                ),
                reauthorization_steps=[
                    f"1. Verify OAuth client credentials exist at {self.credentials_path.name}.",
                    "2. Run: uv run --no-project python -m gmail_mcp.auth.oauth --login",
                ],
            )

        try:
            creds = self.load_credentials(auto_refresh=auto_refresh)
        except PermissionError as exc:
            return AuthStatusReport(
                authenticated=False,
                token_file_exists=True,
                credentials_file_exists=creds_exists,
                token_expired=True,
                has_refresh_token=True,
                granted_scopes=self.get_granted_scopes(),
                status_code="TOKEN_REVOKED_OR_EXPIRED",
                message=str(exc),
                reauthorization_steps=[
                    "1. Run: uv run --no-project python -m gmail_mcp.auth.oauth --login --force-reauth",
                    "2. Complete browser consent to generate a fresh refresh token.",
                ],
            )
        except Exception as exc:
            return AuthStatusReport(
                authenticated=False,
                token_file_exists=True,
                credentials_file_exists=creds_exists,
                status_code="TOKEN_INVALID",
                message=f"Unable to load OAuth token: {type(exc).__name__}",
                reauthorization_steps=[
                    "1. Run: uv run --no-project python -m gmail_mcp.auth.oauth --login --force-reauth"
                ],
            )

        if creds is None:
            return AuthStatusReport(
                authenticated=False,
                token_file_exists=False,
                credentials_file_exists=creds_exists,
                status_code="TOKEN_MISSING",
                message="No OAuth credentials loaded.",
            )

        granted = self.get_granted_scopes()
        granted_set = set(granted)
        missing = (
            []
            if SCOPE_GMAIL_FULL_ACCESS in granted_set
            else [s for s in DEFAULT_SCOPES if s not in granted_set]
        )
        expiry_str = None
        if creds.expiry:
            exp = creds.expiry
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            expiry_str = exp.astimezone(timezone.utc).isoformat()

        is_valid = bool(creds.valid and not creds.expired)
        status_code = "AUTHENTICATED" if is_valid else "TOKEN_EXPIRED"
        if is_valid and missing:
            status_code = "AUTHENTICATED_PARTIAL_SCOPES"

        return AuthStatusReport(
            authenticated=is_valid,
            token_file_exists=True,
            credentials_file_exists=creds_exists,
            token_expired=bool(creds.expired),
            has_refresh_token=bool(creds.refresh_token),
            expiry_utc=expiry_str,
            granted_scopes=granted,
            missing_default_scopes=missing,
            status_code=status_code,
            message=(
                "OAuth token is valid and ready."
                if is_valid and not missing
                else (
                    f"OAuth token is valid but missing recommended scopes: {', '.join(missing)}"
                    if is_valid
                    else "OAuth token is expired and could not be refreshed automatically."
                )
            ),
            reauthorization_steps=(
                [
                    "Run: uv run --no-project python -m gmail_mcp.auth.oauth --login --force-reauth"
                ]
                if (not is_valid or missing)
                else []
            ),
        )
