"""Interactive installed-application OAuth 2.0 flow and CLI helper for personal Gmail accounts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from gmail_mcp.auth.credentials import DEFAULT_SCOPES
from gmail_mcp.auth.token_manager import TokenManager
from gmail_mcp.config import GmailConfig


class _AuthUrlNotifier:
    """Notifies the user and opens the browser via Windows shell when Google generates the OAuth URL."""

    def __init__(self, callback_url_file: Path | None = None) -> None:
        self.callback_url_file = callback_url_file

    def format(self, url: str) -> str:
        msg = (
            f"\n=======================================================\n"
            f"PLEASE VISIT THIS URL TO AUTHORIZE GMAIL MCP:\n{url}\n"
            f"=======================================================\n"
        )
        sys.stdout.write(msg)
        sys.stdout.flush()
        if self.callback_url_file:
            try:
                self.callback_url_file.write_text(url, encoding="utf-8")
            except Exception:
                pass
        try:
            import os
            if hasattr(os, "startfile"):
                os.startfile(url)
        except Exception:
            pass
        return msg


class OAuthFlowManager:
    """Executes Google's installed-application OAuth 2.0 flow for local personal Gmail accounts."""

    def __init__(self, config: GmailConfig, token_manager: TokenManager | None = None) -> None:
        self.config = config
        self.token_manager = token_manager or TokenManager(config)

    def run_interactive_login(
        self,
        scopes: list[str] | None = None,
        port: int = 0,
        force_reauth: bool = False,
        open_browser: bool = True,
    ) -> dict[str, Any]:
        """Run local loopback OAuth 2.0 consent flow and validate via users.getProfile."""
        target_scopes = scopes or list(DEFAULT_SCOPES)

        if not force_reauth:
            existing = self.token_manager.get_auth_status(auto_refresh=True)
            if existing.authenticated and not existing.missing_default_scopes:
                profile = self.verify_with_get_profile()
                return {
                    "success": True,
                    "reused_existing_token": True,
                    "email_address": profile.get("emailAddress"),
                    "history_id": profile.get("historyId"),
                    "scopes": existing.granted_scopes,
                }

        if not self.config.credentials_path.exists():
            raise FileNotFoundError(
                f"OAuth client secret file not found at '{self.config.credentials_path}'. "
                "Download your Desktop App OAuth 2.0 Client ID JSON from Google Cloud Console "
                "(Project 542762227233) and save it as 'credentials.json'."
            )

        url_file = self.config.workspace_root / "data" / "pending_auth_url.txt"
        notifier = _AuthUrlNotifier(callback_url_file=url_file)

        flow = InstalledAppFlow.from_client_secrets_file(
            str(self.config.credentials_path),
            scopes=target_scopes,
        )
        creds = flow.run_local_server(
            port=port,
            access_type="offline",
            prompt="consent",
            open_browser=open_browser,
            authorization_prompt_message=notifier,
        )
        self.token_manager.save_credentials(creds)
        profile = self.verify_with_get_profile()
        return {
            "success": True,
            "reused_existing_token": False,
            "email_address": profile.get("emailAddress"),
            "messages_total": profile.get("messagesTotal"),
            "threads_total": profile.get("threadsTotal"),
            "history_id": profile.get("historyId"),
            "scopes": self.token_manager.get_granted_scopes(),
        }

    def verify_with_get_profile(self) -> dict[str, Any]:
        """Validate active OAuth credentials by calling Gmail REST API users.getProfile."""
        creds = self.token_manager.load_credentials(auto_refresh=True)
        if creds is None or not creds.valid:
            raise PermissionError("OAuth credentials are missing or invalid; cannot call users.getProfile.")
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        profile: dict[str, Any] = service.users().getProfile(userId="me").execute()
        return profile


def main() -> int:
    parser = argparse.ArgumentParser(description="Gmail Automation MCP Server OAuth 2.0 Helper")
    parser.add_argument("--login", action="store_true", help="Run interactive local browser OAuth login")
    parser.add_argument("--force-reauth", action="store_true", help="Force new consent and refresh token")
    parser.add_argument("--status", action="store_true", help="Print current OAuth token & profile status")
    parser.add_argument("--port", type=int, default=0, help="Local loopback port (default: 0 / OS-assigned)")
    parser.add_argument("--credentials-path", type=str, default=None, help="Path to client secrets JSON")
    args = parser.parse_args()

    config = GmailConfig()
    if args.credentials_path:
        config.credentials_path = Path(args.credentials_path).resolve()
    token_mgr = TokenManager(config)
    flow_mgr = OAuthFlowManager(config, token_mgr)

    if args.login:
        result = flow_mgr.run_interactive_login(port=args.port, force_reauth=args.force_reauth)
        print(json.dumps(result, indent=2))
        return 0

    status = token_mgr.get_auth_status(auto_refresh=True)
    output: dict[str, Any] = status.model_dump()
    if status.authenticated:
        try:
            profile = flow_mgr.verify_with_get_profile()
            output["profile"] = {
                "emailAddress": profile.get("emailAddress"),
                "messagesTotal": profile.get("messagesTotal"),
                "threadsTotal": profile.get("threadsTotal"),
                "historyId": profile.get("historyId"),
            }
        except Exception as exc:
            output["profile_error"] = f"{type(exc).__name__}: {exc}"
    print(json.dumps(output, indent=2))
    return 0 if status.authenticated else 1


if __name__ == "__main__":
    sys.exit(main())
