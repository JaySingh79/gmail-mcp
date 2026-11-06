"""SQLite-backed email template engine tools (`gmail_*_template`)."""

from __future__ import annotations

import re
from typing import Any

from gmail_mcp.core.gmail_client import GmailClientWrapper
from gmail_mcp.core.safety import SafetyError


def _slugify_template_id(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "template"


async def gmail_create_template(
    client: GmailClientWrapper,
    name: str,
    subject: str,
    *,
    template_id: str | None = None,
    body_text: str | None = None,
    body_html: str | None = None,
    variables: list[str] | None = None,
    defaults: dict[str, str] | None = None,
    attachments: list[str] | None = None,
) -> dict[str, Any]:
    """Create a reusable email template backed by SQLite with automatic placeholder discovery."""
    try:
        if not name or not name.strip():
            raise SafetyError("INVALID_TEMPLATE_NAME", "Template `name` cannot be empty.")
        if not subject or not subject.strip():
            raise SafetyError("INVALID_TEMPLATE_SUBJECT", "Template `subject` cannot be empty.")
        if body_text is None and body_html is None:
            raise SafetyError("EMPTY_TEMPLATE_BODY", "Either `body_text` or `body_html` must be provided.")

        tid = (template_id.strip() if template_id else _slugify_template_id(name))
        extracted_vars, inline_defaults = client.safety.extract_template_variables(
            subject, body_text, body_html
        )
        merged_vars = list(dict.fromkeys((variables or []) + extracted_vars))
        merged_defaults = {**inline_defaults, **(defaults or {})}

        # Validate attachment paths if provided
        validated_attachments: list[str] = []
        for att in attachments or []:
            info = client.safety.validate_attachment_file(att)
            validated_attachments.append(str(info["path"]))

        record = client.template_repo.create(
            template_id=tid,
            name=name.strip(),
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            variables=merged_vars,
            defaults=merged_defaults,
            attachments=validated_attachments,
        )
        return {
            "success": True,
            "operation": "gmail_create_template",
            "mutates_mailbox": False,
            "template": record.model_dump(),
            "quota_estimate": {"units_per_request": 0, "total_units": 0},
        }
    except Exception as exc:
        return client.format_tool_error("gmail_create_template", exc, mutates_mailbox=False)


async def gmail_get_template(
    client: GmailClientWrapper,
    template_id: str,
    *,
    version: int | None = None,
) -> dict[str, Any]:
    """Retrieve a template by ID and optional version number."""
    try:
        record = client.template_repo.get(template_id, version=version)
        if not record:
            return {
                "success": False,
                "operation": "gmail_get_template",
                "mutates_mailbox": False,
                "error_code": "TEMPLATE_NOT_FOUND",
                "message": f"Template '{template_id}' (version={version or 'latest'}) not found.",
                "http_status": 404,
                "retryable": False,
            }
        return {
            "success": True,
            "operation": "gmail_get_template",
            "mutates_mailbox": False,
            "template": record.model_dump(),
            "quota_estimate": {"units_per_request": 0, "total_units": 0},
        }
    except Exception as exc:
        return client.format_tool_error("gmail_get_template", exc, mutates_mailbox=False)


async def gmail_list_templates(client: GmailClientWrapper) -> dict[str, Any]:
    """List all saved email templates in SQLite."""
    try:
        records = client.template_repo.list_all()
        return {
            "success": True,
            "operation": "gmail_list_templates",
            "mutates_mailbox": False,
            "count": len(records),
            "templates": [r.model_dump() for r in records],
            "quota_estimate": {"units_per_request": 0, "total_units": 0},
        }
    except Exception as exc:
        return client.format_tool_error("gmail_list_templates", exc, mutates_mailbox=False)


async def gmail_update_template(
    client: GmailClientWrapper,
    template_id: str,
    *,
    name: str | None = None,
    subject: str | None = None,
    body_text: str | None = None,
    body_html: str | None = None,
    variables: list[str] | None = None,
    defaults: dict[str, str] | None = None,
    attachments: list[str] | None = None,
) -> dict[str, Any]:
    """Update an existing template and increment its version number in SQLite."""
    try:
        existing = client.template_repo.get(template_id)
        if not existing:
            return {
                "success": False,
                "operation": "gmail_update_template",
                "mutates_mailbox": False,
                "error_code": "TEMPLATE_NOT_FOUND",
                "message": f"Template '{template_id}' not found.",
                "http_status": 404,
                "retryable": False,
            }

        eff_subject = subject if subject is not None else existing.subject
        eff_text = body_text if body_text is not None else existing.body_text
        eff_html = body_html if body_html is not None else existing.body_html
        extracted_vars, inline_defaults = client.safety.extract_template_variables(
            eff_subject, eff_text, eff_html
        )
        merged_vars = list(dict.fromkeys((variables if variables is not None else existing.variables) + extracted_vars))
        merged_defaults = {**existing.defaults, **inline_defaults, **(defaults or {})}

        validated_attachments: list[str] | None = None
        if attachments is not None:
            validated_attachments = []
            for att in attachments:
                info = client.safety.validate_attachment_file(att)
                validated_attachments.append(str(info["path"]))

        updated = client.template_repo.update(
            template_id=template_id,
            name=name,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            variables=merged_vars,
            defaults=merged_defaults,
            attachments=validated_attachments,
        )
        return {
            "success": True,
            "operation": "gmail_update_template",
            "mutates_mailbox": False,
            "template": updated.model_dump(),
            "quota_estimate": {"units_per_request": 0, "total_units": 0},
        }
    except Exception as exc:
        return client.format_tool_error("gmail_update_template", exc, mutates_mailbox=False)


async def gmail_delete_template(
    client: GmailClientWrapper,
    template_id: str,
) -> dict[str, Any]:
    """Delete a template and its version history from SQLite."""
    try:
        deleted = client.template_repo.delete(template_id)
        if not deleted:
            return {
                "success": False,
                "operation": "gmail_delete_template",
                "mutates_mailbox": False,
                "error_code": "TEMPLATE_NOT_FOUND",
                "message": f"Template '{template_id}' not found.",
                "http_status": 404,
                "retryable": False,
            }
        return {
            "success": True,
            "operation": "gmail_delete_template",
            "mutates_mailbox": False,
            "template_id": template_id,
            "deleted": True,
            "quota_estimate": {"units_per_request": 0, "total_units": 0},
        }
    except Exception as exc:
        return client.format_tool_error("gmail_delete_template", exc, mutates_mailbox=False)


async def gmail_preview_template(
    client: GmailClientWrapper,
    template_id: str,
    variables: dict[str, Any] | None = None,
    *,
    version: int | None = None,
) -> dict[str, Any]:
    """
    Render a template with supplied variables.
    Enforces strict missing-variable validation and HTML escaping for untrusted variables.
    """
    try:
        record = client.template_repo.get(template_id, version=version)
        if not record:
            return {
                "success": False,
                "operation": "gmail_preview_template",
                "mutates_mailbox": False,
                "error_code": "TEMPLATE_NOT_FOUND",
                "message": f"Template '{template_id}' (version={version or 'latest'}) not found.",
                "http_status": 404,
                "retryable": False,
            }

        rendered = client.safety.render_template_content(
            subject=record.subject,
            body_text=record.body_text,
            body_html=record.body_html,
            variables=variables,
            defaults=record.defaults,
            required_variables=record.variables,
        )
        return {
            "success": True,
            "operation": "gmail_preview_template",
            "mutates_mailbox": False,
            "template_id": record.template_id,
            "version": record.version,
            "rendered_subject": rendered["subject"],
            "rendered_body_text": rendered["body_text"],
            "rendered_body_html": rendered["body_html"],
            "attachments": record.attachments,
            "quota_estimate": {"units_per_request": 0, "total_units": 0},
        }
    except Exception as exc:
        return client.format_tool_error("gmail_preview_template", exc, mutates_mailbox=False)
