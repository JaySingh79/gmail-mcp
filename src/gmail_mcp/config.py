"""Configuration and authoritative quota registry for the Gmail Automation MCP Server."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

def _find_project_root() -> Path:
    current = Path(__file__).resolve().parent
    for p in [current, *current.parents]:
        if (p / "pyproject.toml").exists() or (p / ".git").exists():
            return p
    # Fallback: src/gmail_mcp is 2 levels below repo root
    try:
        return Path(__file__).resolve().parents[2]
    except IndexError:
        return current

WORKSPACE_ROOT = _find_project_root()

# Authoritative Gmail REST API v1 Quota Cost Registry (Units per request)
# Reference: https://developers.google.com/workspace/gmail/api/reference/quota
QUOTA_COST_REGISTRY: dict[str, int] = {
    "users.getProfile": 1,
    "messages.list": 5,
    "messages.get.metadata": 20,
    "messages.get.minimal": 20,
    "messages.get.full": 60,
    "messages.get.raw": 60,
    "messages.get.html": 60,
    "messages.send": 100,
    "messages.modify": 5,
    "messages.trash": 5,
    "messages.untrash": 5,
    "messages.delete": 10,
    "messages.batchModify": 50,
    "messages.batchDelete": 50,
    "messages.attachments.get": 20,
    "drafts.create": 10,
    "drafts.get.minimal": 20,
    "drafts.get.metadata": 20,
    "drafts.get.full": 60,
    "drafts.get.raw": 60,
    "drafts.get.html": 60,
    "drafts.list": 5,
    "drafts.update": 15,
    "drafts.delete": 10,
    "drafts.send": 100,
    "threads.list": 10,
    "threads.get.minimal": 40,
    "threads.get.metadata": 40,
    "threads.get.full": 120,
    "threads.modify": 10,
    "threads.trash": 10,
    "threads.untrash": 10,
    "threads.delete": 20,
    "history.list": 2,
    "watch": 100,
    "stop": 50,
    "labels.create": 5,
    "labels.list": 1,
    "labels.get": 1,
    "labels.update": 5,
    "labels.patch": 5,
    "labels.delete": 5,
    "settings.filters.create": 5,
    "settings.filters.list": 1,
    "settings.filters.get": 1,
    "settings.filters.delete": 5,
    "settings.getVacation": 1,
    "settings.updateVacation": 5,
    "settings.getAutoForwarding": 1,
    "settings.sendAs.list": 1,
    "settings.sendAs.get": 1,
}

# Recommended application-level safeguard defaults (Section 7.D)
GMAIL_CONFIG: dict[str, Any] = {
    "account_type": "personal",
    "daily_send_soft_cap": 200,
    "daily_send_hard_cap": 300,
    "send_concurrency": 1,
    "send_interval_seconds": 3,
    "read_concurrency": 3,
    "write_concurrency": 2,
    "batch_size": 25,
    "max_api_batch_size": 50,
    "max_retries": 5,
    "max_backoff_seconds": 64,
    "search_page_size": 500,
    "attachment_warning_size_mb": 18,
    "attachment_max_size_mb": 25,
    "quota_safety_margin": 0.20,
}


def get_quota_cost(method: str, fmt: str | None = None) -> int:
    """Resolve the quota unit cost for a Gmail API method and optional format."""
    if fmt:
        fmt_lower = fmt.lower()
        key = f"{method}.{fmt_lower}"
        if key in QUOTA_COST_REGISTRY:
            return QUOTA_COST_REGISTRY[key]
    if method in QUOTA_COST_REGISTRY:
        return QUOTA_COST_REGISTRY[method]
    return 5


class GmailConfig(BaseModel):
    """Typed, configurable runtime settings for the Gmail Automation MCP Server."""

    provider: str = "Gmail"
    account_type: Literal["personal", "workspace"] = Field(
        default_factory=lambda: os.getenv("GMAIL_ACCOUNT_TYPE", "personal")  # type: ignore[arg-type]
    )
    api_version: str = "v1"
    cloud_project_number: str = Field(
        default_factory=lambda: os.getenv("GMAIL_CLOUD_PROJECT_NUMBER", "187614359153")
    )
    cloud_project_created_before: str = "2026-05-01"
    legacy_quota_verified: bool = Field(
        default_factory=lambda: os.getenv("GMAIL_LEGACY_QUOTA_VERIFIED", "0") == "1"
    )

    # Paths
    workspace_root: Path = Field(default=WORKSPACE_ROOT)
    credentials_path: Path = Field(
        default_factory=lambda: Path(
            os.getenv("GMAIL_CREDENTIALS_PATH", str(WORKSPACE_ROOT / "creds_mcp_agy copy.json"))
        )
    )
    token_path: Path = Field(
        default_factory=lambda: Path(
            os.getenv("GMAIL_TOKEN_PATH", str(WORKSPACE_ROOT / "token.json"))
        )
    )
    db_path: Path = Field(
        default_factory=lambda: Path(
            os.getenv(
                "GMAIL_DB_PATH",
                str(WORKSPACE_ROOT / "data" / "gmail_mcp.db"),
            )
        )
    )
    allowed_dirs: list[Path] = Field(
        default_factory=lambda: [
            (WORKSPACE_ROOT / d.strip()).resolve()
            for d in os.getenv(
                "GMAIL_ALLOWED_DIRS",
                "attachments,data/downloads,data/exports",
            ).split(",")
            if d.strip()
        ]
    )
    allow_any_attachment_dir: bool = Field(
        default_factory=lambda: os.getenv("GMAIL_ALLOW_ANY_ATTACHMENT_DIR", "true").lower() in ("1", "true", "yes")
    )
    allow_any_dir: bool = Field(
        default_factory=lambda: os.getenv("GMAIL_ALLOW_ANY_DIR", "false").lower() in ("1", "true", "yes")
    )

    # Standard documented quota model (Section 7.A)
    project_quota_per_minute: int = Field(
        default_factory=lambda: int(os.getenv("GMAIL_PROJECT_QUOTA_PER_MINUTE", "1200000"))
    )
    user_quota_per_minute: int = Field(
        default_factory=lambda: int(os.getenv("GMAIL_USER_QUOTA_PER_MINUTE", "6000"))
    )
    daily_billing_threshold: int = Field(
        default_factory=lambda: int(os.getenv("GMAIL_DAILY_BILLING_THRESHOLD", "80000000"))
    )
    quota_safety_margin: float = Field(
        default_factory=lambda: float(os.getenv("GMAIL_QUOTA_SAFETY_MARGIN", "0.20"))
    )

    # Personal Gmail external ceilings & application caps (Section 7.C & 7.D)
    external_daily_send_ceiling: int = 500
    max_recipients_per_message: int = 500
    daily_send_soft_cap: int = Field(
        default_factory=lambda: int(os.getenv("GMAIL_DAILY_SEND_SOFT_CAP", "200"))
    )
    daily_send_hard_cap: int = Field(
        default_factory=lambda: int(os.getenv("GMAIL_DAILY_SEND_HARD_CAP", "300"))
    )

    # Concurrency & pacing
    send_concurrency: int = Field(
        default_factory=lambda: int(os.getenv("GMAIL_SEND_CONCURRENCY", "1"))
    )
    send_interval_seconds: float = Field(
        default_factory=lambda: float(os.getenv("GMAIL_SEND_INTERVAL_SECONDS", "3.0"))
    )
    read_concurrency: int = Field(
        default_factory=lambda: int(os.getenv("GMAIL_READ_CONCURRENCY", "3"))
    )
    write_concurrency: int = Field(
        default_factory=lambda: int(os.getenv("GMAIL_WRITE_CONCURRENCY", "2"))
    )

    # Batching & pagination
    batch_size: int = Field(
        default_factory=lambda: int(os.getenv("GMAIL_BATCH_SIZE", "25"))
    )
    max_api_batch_size: int = Field(
        default_factory=lambda: int(os.getenv("GMAIL_MAX_API_BATCH_SIZE", "50"))
    )
    search_page_size: int = Field(
        default_factory=lambda: int(os.getenv("GMAIL_SEARCH_PAGE_SIZE", "500"))
    )

    # Retry & backoff
    max_retries: int = Field(
        default_factory=lambda: int(os.getenv("GMAIL_MAX_RETRIES", "5"))
    )
    max_backoff_seconds: float = Field(
        default_factory=lambda: float(os.getenv("GMAIL_MAX_BACKOFF_SECONDS", "64.0"))
    )

    # Attachment size limits
    attachment_warning_size_mb: float = Field(
        default_factory=lambda: float(os.getenv("GMAIL_ATTACHMENT_WARNING_SIZE_MB", "18.0"))
    )
    attachment_max_size_mb: float = Field(
        default_factory=lambda: float(os.getenv("GMAIL_ATTACHMENT_MAX_SIZE_MB", "25.0"))
    )

    # Optional Pub/Sub push watch configuration
    pubsub_topic: str | None = Field(
        default_factory=lambda: os.getenv("GMAIL_PUBSUB_TOPIC") or None
    )

    # E2E testing gates
    e2e_enabled: bool = Field(
        default_factory=lambda: os.getenv("GMAIL_E2E_ENABLED", "0") == "1"
    )
    e2e_send_enabled: bool = Field(
        default_factory=lambda: os.getenv("GMAIL_E2E_SEND_ENABLED", "0") == "1"
    )
    e2e_allowed_recipients: list[str] = Field(
        default_factory=lambda: [
            r.strip().lower()
            for r in os.getenv("GMAIL_E2E_ALLOWED_RECIPIENTS", "").split(",")
            if r.strip()
        ]
    )

    @property
    def effective_user_quota_per_minute(self) -> int:
        return int(self.user_quota_per_minute * (1.0 - self.quota_safety_margin))

    @property
    def effective_project_quota_per_minute(self) -> int:
        return int(self.project_quota_per_minute * (1.0 - self.quota_safety_margin))

    @property
    def attachment_warning_bytes(self) -> int:
        return int(self.attachment_warning_size_mb * 1024 * 1024)

    @property
    def attachment_max_bytes(self) -> int:
        return int(self.attachment_max_size_mb * 1024 * 1024)

    def ensure_directories(self) -> None:
        """Ensure database parent directory and default allowed directories exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        for directory in self.allowed_dirs:
            if str(directory) != "*" and getattr(directory, "name", "") != "*":
                directory.mkdir(parents=True, exist_ok=True)
