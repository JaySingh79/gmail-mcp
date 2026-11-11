"""Security, filesystem, recipient, template, and logging safeguards."""

from __future__ import annotations

import html
import logging
import re
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from gmail_mcp.config import GmailConfig

logger = logging.getLogger("gmail_mcp.audit")

EMAIL_REGEX = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$"
)
TEMPLATE_VAR_REGEX = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)(?:\|([^{}]*))?\s*\}\}")
SENSITIVE_PATTERNS = [
    re.compile(r"ya29\.[A-Za-z0-9_\-]+"),
    re.compile(r"1//[A-Za-z0-9_\-]+"),
    re.compile(r'"(?:access_token|refresh_token|client_secret)"\s*:\s*"[^"]+"'),
]


class SafetyError(ValueError):
    """Raised when a security, filesystem, recipient, or template safety check fails."""

    def __init__(self, error_code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.details = details or {}


class SafetyGuard:
    """Enforces email, filesystem, template, sending, and logging safety controls."""

    def __init__(self, config: GmailConfig) -> None:
        self.config = config

    # --- Email & Recipient Safety ---

    @staticmethod
    def extract_email_address(raw_address: str) -> str:
        """Extract and normalize the bare email address from 'Name <user@domain>' or 'user@domain'."""
        if not raw_address or not raw_address.strip():
            raise SafetyError("INVALID_RECIPIENT", "Recipient email address cannot be empty.")
        _, addr = parseaddr(raw_address.strip())
        addr = addr.strip()
        if not addr or not EMAIL_REGEX.match(addr):
            raise SafetyError(
                "INVALID_RECIPIENT",
                f"Invalid or ambiguous email address: '{raw_address}'.",
                {"address": raw_address},
            )
        return addr

    def validate_and_deduplicate_recipients(
        self,
        to: list[str] | str | None,
        cc: list[str] | str | None = None,
        bcc: list[str] | str | None = None,
    ) -> dict[str, Any]:
        """Validate recipient addresses, detect duplicates, enforce the 500-recipient limit, and protect BCC."""
        to_list = [to] if isinstance(to, str) else list(to or [])
        cc_list = [cc] if isinstance(cc, str) else list(cc or [])
        bcc_list = [bcc] if isinstance(bcc, str) else list(bcc or [])

        seen: set[str] = set()
        duplicates: list[str] = []

        def _process(items: list[str]) -> list[str]:
            cleaned: list[str] = []
            for item in items:
                if not item or not item.strip():
                    continue
                addr = self.extract_email_address(item)
                lower = addr.lower()
                if lower in seen:
                    duplicates.append(addr)
                    continue
                seen.add(lower)
                cleaned.append(item.strip())
            return cleaned

        clean_to = _process(to_list)
        clean_cc = _process(cc_list)
        clean_bcc = _process(bcc_list)

        total_count = len(clean_to) + len(clean_cc) + len(clean_bcc)
        if total_count == 0:
            raise SafetyError("MISSING_RECIPIENTS", "At least one valid recipient is required.")
        if total_count > self.config.max_recipients_per_message:
            raise SafetyError(
                "TOO_MANY_RECIPIENTS",
                f"Message has {total_count} recipients, exceeding the Gmail maximum of {self.config.max_recipients_per_message}.",
                {"total_recipients": total_count, "max_allowed": self.config.max_recipients_per_message},
            )

        if self.config.e2e_enabled and self.config.e2e_allowed_recipients:
            allowset = set(self.config.e2e_allowed_recipients)
            for raw in clean_to + clean_cc + clean_bcc:
                bare = self.extract_email_address(raw).lower()
                if bare not in allowset:
                    raise SafetyError(
                        "RECIPIENT_NOT_IN_TEST_ALLOWLIST",
                        f"Recipient '{bare}' is not in GMAIL_E2E_ALLOWED_RECIPIENTS.",
                        {"recipient": bare},
                    )

        return {
            "to": clean_to,
            "cc": clean_cc,
            "bcc": clean_bcc,
            "bcc_count": len(clean_bcc),
            "total_count": total_count,
            "duplicates_removed": duplicates,
            # User-facing summary never exposes actual BCC addresses
            "public_summary": {
                "to": clean_to,
                "cc": clean_cc,
                "bcc_count": len(clean_bcc),
                "total_recipients": total_count,
            },
        }

    # --- Filesystem & Attachment Safety ---

    def resolve_safe_path(
        self,
        file_path: str | Path,
        must_exist: bool = True,
        allow_create_in_allowed_dir: bool = False,
        is_attachment: bool = False,
    ) -> Path:
        """Resolve a path and enforce allowed-directory containment, path traversal protection, and symlink blocking."""
        raw_path = Path(file_path)
        raw_str = str(file_path)
        if ".." in Path(raw_str).parts:
            raise SafetyError(
                "PATH_TRAVERSAL_BLOCKED",
                f"Path traversal sequence ('..') is forbidden: '{file_path}'.",
            )

        candidates: list[Path] = []
        if raw_path.is_absolute():
            candidates.append(raw_path)
        else:
            candidates.append(self.config.workspace_root / raw_path)
            try:
                candidates.append(Path.cwd() / raw_path)
            except Exception:
                pass
            for allowed in self.config.allowed_dirs:
                if str(allowed) != "*" and getattr(allowed, "name", "") != "*":
                    candidates.append(allowed / raw_path)

        chosen: Path | None = None
        for cand in candidates:
            if cand.exists() or cand.is_symlink():
                chosen = cand
                break
        if chosen is None:
            chosen = candidates[0]

        # Reject symlinks anywhere along the target or its immediate path
        if chosen.is_symlink():
            raise SafetyError(
                "UNSAFE_SYMLINK_BLOCKED",
                f"Symlink paths are forbidden for attachment/export operations: '{file_path}'.",
            )

        resolved = chosen.resolve()

        # Check allowed directory boundary
        skip_dir_check = False
        if is_attachment and getattr(self.config, "allow_any_attachment_dir", True):
            skip_dir_check = True
        elif getattr(self.config, "allow_any_dir", False):
            skip_dir_check = True
        elif any(str(d) == "*" or getattr(d, "name", "") == "*" for d in self.config.allowed_dirs):
            skip_dir_check = True

        if not skip_dir_check:
            allowed_resolved = [
                d.resolve()
                for d in self.config.allowed_dirs
                if str(d) != "*" and getattr(d, "name", "") != "*"
            ]
            if not any(resolved == allowed_dir or allowed_dir in resolved.parents for allowed_dir in allowed_resolved):
                raise SafetyError(
                    "PATH_OUTSIDE_ALLOWED_DIRS",
                    f"Path '{file_path}' resolves outside configured allowed directories.",
                    {"allowed_directories": [str(d) for d in allowed_resolved]},
                )

        if must_exist:
            if not resolved.exists() or not resolved.is_file():
                raise SafetyError(
                    "ATTACHMENT_FILE_NOT_FOUND",
                    f"Attachment or input file does not exist or is not a regular file: '{file_path}'.",
                )
            if not os_access_readable(resolved):
                raise SafetyError(
                    "ATTACHMENT_FILE_UNREADABLE",
                    f"File is not readable: '{file_path}'.",
                )
        elif allow_create_in_allowed_dir:
            resolved.parent.mkdir(parents=True, exist_ok=True)

        return resolved

    def validate_attachment_file(self, file_path: str | Path) -> dict[str, Any]:
        """Validate attachment existence, path safety, and raw file size."""
        resolved = self.resolve_safe_path(file_path, must_exist=True, is_attachment=True)
        size_bytes = resolved.stat().st_size
        if size_bytes > self.config.attachment_max_bytes:
            raise SafetyError(
                "ATTACHMENT_SIZE_EXCEEDED",
                (
                    f"Attachment '{resolved.name}' is {size_bytes / (1024 * 1024):.2f} MB, "
                    f"exceeding the {self.config.attachment_max_size_mb} MB ceiling."
                ),
                {"filename": resolved.name, "size_bytes": size_bytes},
            )
        warning = None
        if size_bytes >= self.config.attachment_warning_bytes:
            warning = (
                f"Attachment '{resolved.name}' ({size_bytes / (1024 * 1024):.2f} MB) exceeds the "
                f"{self.config.attachment_warning_size_mb} MB operational warning threshold."
            )
        return {
            "path": resolved,
            "filename": resolved.name,
            "size_bytes": size_bytes,
            "warning": warning,
        }

    def validate_encoded_mime_size(self, encoded_bytes_len: int) -> str | None:
        """Ensure total encoded MIME message does not exceed the 25 MB personal Gmail ceiling."""
        if encoded_bytes_len > self.config.attachment_max_bytes:
            raise SafetyError(
                "MIME_MESSAGE_SIZE_EXCEEDED",
                (
                    f"Encoded MIME message size ({encoded_bytes_len / (1024 * 1024):.2f} MB) "
                    f"exceeds the {self.config.attachment_max_size_mb} MB Gmail limit."
                ),
                {"encoded_size_bytes": encoded_bytes_len},
            )
        if encoded_bytes_len >= self.config.attachment_warning_bytes:
            return (
                f"Encoded MIME message size ({encoded_bytes_len / (1024 * 1024):.2f} MB) is above "
                f"the {self.config.attachment_warning_size_mb} MB operational warning threshold."
            )
        return None

    # --- Template Rendering & HTML Escaping ---

    @staticmethod
    def extract_template_variables(
        subject: str | None,
        body_text: str | None,
        body_html: str | None,
    ) -> tuple[list[str], dict[str, str]]:
        """Extract variable names and inline default values (`{{var|default}}`) from template strings."""
        vars_found: list[str] = []
        defaults: dict[str, str] = {}
        for content in (subject or "", body_text or "", body_html or ""):
            for match in TEMPLATE_VAR_REGEX.finditer(content):
                var_name = match.group(1)
                default_val = match.group(2)
                if var_name not in vars_found:
                    vars_found.append(var_name)
                if default_val is not None:
                    defaults[var_name] = default_val
        return vars_found, defaults

    @classmethod
    def render_template_content(
        cls,
        subject: str,
        body_text: str | None,
        body_html: str | None,
        variables: dict[str, Any] | None = None,
        defaults: dict[str, str] | None = None,
        required_variables: list[str] | None = None,
    ) -> dict[str, str | None]:
        """Render subject, plaintext, and HTML bodies while validating missing variables and escaping HTML."""
        supplied = {k: v for k, v in (variables or {}).items() if v is not None}
        fallback = dict(defaults or {})

        extracted_vars, inline_defaults = cls.extract_template_variables(subject, body_text, body_html)
        for k, v in inline_defaults.items():
            fallback.setdefault(k, v)

        all_required = list(dict.fromkeys((required_variables or []) + extracted_vars))
        missing = [
            var
            for var in all_required
            if var not in supplied and var not in fallback
        ]
        if missing:
            raise SafetyError(
                "MISSING_TEMPLATE_VARIABLE",
                f"Missing required template variable(s): {', '.join(missing)}",
                {"missing_variables": missing},
            )

        def _replace(text: str | None, escape_html_values: bool) -> str | None:
            if text is None:
                return None

            def _sub(match: re.Match[str]) -> str:
                var_name = match.group(1)
                inline_def = match.group(2)
                if var_name in supplied:
                    raw_val = str(supplied[var_name])
                elif var_name in fallback:
                    raw_val = str(fallback[var_name])
                elif inline_def is not None:
                    raw_val = inline_def
                else:
                    raise SafetyError(
                        "MISSING_TEMPLATE_VARIABLE",
                        f"Missing required template variable: '{var_name}'",
                        {"missing_variables": [var_name]},
                    )
                return html.escape(raw_val, quote=True) if escape_html_values else raw_val

            return TEMPLATE_VAR_REGEX.sub(_sub, text)

        return {
            "subject": _replace(subject, escape_html_values=False) or "",
            "body_text": _replace(body_text, escape_html_values=False),
            "body_html": _replace(body_html, escape_html_values=True),
        }

    # --- Redacting Audit Logger ---

    @staticmethod
    def redact_text(value: str) -> str:
        """Strip any accidental OAuth token or secret patterns from log messages."""
        redacted = value
        for pattern in SENSITIVE_PATTERNS:
            redacted = pattern.sub("[REDACTED]", redacted)
        return redacted

    @classmethod
    def log_operation(
        cls,
        *,
        tool_name: str,
        operation_id: str,
        api_method: str,
        duration_ms: float,
        http_status: int,
        quota_units: int,
        success: bool,
        retry_count: int = 0,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        """Emit a structured audit log entry without tokens, secrets, email bodies, or attachments."""
        entry = {
            "tool_name": tool_name,
            "operation_id": operation_id,
            "api_method": api_method,
            "duration_ms": round(duration_ms, 2),
            "http_status": http_status,
            "quota_estimate": quota_units,
            "success": success,
            "retry_count": retry_count,
            "error_code": error_code,
        }
        logger.info("gmail_mcp_audit %s", entry)
        return entry


def os_access_readable(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            fh.read(1)
        return True
    except OSError:
        return False
