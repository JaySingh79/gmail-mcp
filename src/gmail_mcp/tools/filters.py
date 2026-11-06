"""Gmail filter management tools (`list`, `get`, `create`, `delete`)."""

from __future__ import annotations

from typing import Any

from gmail_mcp.core.gmail_client import GmailClientWrapper
from gmail_mcp.core.safety import SafetyError


async def gmail_list_filters(client: GmailClientWrapper) -> dict[str, Any]:
    """List all Gmail filters configured on the account (`settings.filters.list`)."""
    try:
        resp, meta = await client.execute_api(
            "settings.filters.list",
            {},
            tool_name="gmail_list_filters",
            op_class="read",
        )
        filters = resp.get("filter") or []
        return {
            "success": True,
            "operation": "gmail_list_filters",
            "mutates_mailbox": False,
            "count": len(filters),
            "filters": filters,
            "quota_estimate": {
                "units_per_request": 1,
                "total_units": int(meta.get("quota_estimate", 1)),
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_list_filters", exc, mutates_mailbox=False)


async def gmail_get_filter(
    client: GmailClientWrapper,
    filter_id: str,
) -> dict[str, Any]:
    """Retrieve details for a specific Gmail filter by ID."""
    try:
        resp, meta = await client.execute_api(
            "settings.filters.get",
            {"id": filter_id},
            tool_name="gmail_get_filter",
            op_class="read",
        )
        return {
            "success": True,
            "operation": "gmail_get_filter",
            "mutates_mailbox": False,
            "filter": resp,
            "quota_estimate": {
                "units_per_request": 1,
                "total_units": int(meta.get("quota_estimate", 1)),
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_get_filter", exc, mutates_mailbox=False)


async def gmail_create_filter(
    client: GmailClientWrapper,
    criteria: dict[str, Any],
    actions: dict[str, Any],
    *,
    confirm_forward: bool = False,
) -> dict[str, Any]:
    """
    Create a new Gmail filter after validating matching criteria and actions.
    Supports friendly action flags (`mark_read`, `archive`, `star`) as well as raw Gmail `addLabelIds`/`removeLabelIds`.
    Never adds a forwarding rule silently (`confirm_forward=True` required if `forward` is present).
    """
    try:
        if not criteria or not isinstance(criteria, dict):
            raise SafetyError("INVALID_FILTER_CRITERIA", "`criteria` must be a non-empty dictionary.")
        if not actions or not isinstance(actions, dict):
            raise SafetyError("INVALID_FILTER_ACTIONS", "`actions` must be a non-empty dictionary.")

        norm_criteria: dict[str, Any] = {}
        allowed_criteria_keys = {
            "from": "from",
            "to": "to",
            "subject": "subject",
            "query": "query",
            "contains_words": "query",
            "negatedQuery": "negatedQuery",
            "does_not_contain_words": "negatedQuery",
            "hasAttachment": "hasAttachment",
            "has_attachment": "hasAttachment",
            "excludeChats": "excludeChats",
            "size": "size",
            "sizeComparison": "sizeComparison",
            "size_comparison": "sizeComparison",
        }
        for k, v in criteria.items():
            if v is None or v == "":
                continue
            mapped = allowed_criteria_keys.get(k)
            if not mapped:
                raise SafetyError("UNKNOWN_FILTER_CRITERIA_FIELD", f"Unsupported filter criteria field: '{k}'.")
            norm_criteria[mapped] = v

        if not norm_criteria:
            raise SafetyError("EMPTY_FILTER_CRITERIA", "At least one valid filter criterion must be provided.")

        add_labels: list[str] = list(actions.get("addLabelIds") or [])
        remove_labels: list[str] = list(actions.get("removeLabelIds") or [])

        if actions.get("mark_read") or actions.get("markRead"):
            if "UNREAD" not in remove_labels:
                remove_labels.append("UNREAD")
        if actions.get("archive"):
            if "INBOX" not in remove_labels:
                remove_labels.append("INBOX")
        if actions.get("star"):
            if "STARRED" not in add_labels:
                add_labels.append("STARRED")
        if actions.get("apply_label"):
            lbl = str(actions["apply_label"])
            if lbl not in add_labels:
                add_labels.append(lbl)

        norm_action: dict[str, Any] = {}
        if add_labels:
            norm_action["addLabelIds"] = add_labels
        if remove_labels:
            norm_action["removeLabelIds"] = remove_labels

        if actions.get("forward"):
            fwd_addr = client.safety.extract_email_address(str(actions["forward"]))
            if not confirm_forward:
                raise SafetyError(
                    "FORWARD_FILTER_CONFIRMATION_REQUIRED",
                    (
                        f"Creating a filter that forwards emails to '{fwd_addr}' requires explicit "
                        "`confirm_forward=True` and a verified forwarding address."
                    ),
                    {"forward_address": fwd_addr},
                )
            norm_action["forward"] = fwd_addr

        if not norm_action:
            raise SafetyError(
                "EMPTY_FILTER_ACTIONS",
                "At least one valid filter action (`addLabelIds`, `removeLabelIds`, `mark_read`, `archive`, `star`, `forward`) is required.",
            )

        filter_body = {"criteria": norm_criteria, "action": norm_action}
        resp, meta = await client.execute_api(
            "settings.filters.create",
            {"body": filter_body},
            tool_name="gmail_create_filter",
            op_class="write",
        )
        return {
            "success": True,
            "operation": "gmail_create_filter",
            "mutates_mailbox": True,
            "filter_id": resp.get("id"),
            "filter": resp,
            "quota_estimate": {
                "units_per_request": 5,
                "total_units": int(meta.get("quota_estimate", 5)),
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_create_filter", exc, mutates_mailbox=True)


async def gmail_delete_filter(
    client: GmailClientWrapper,
    filter_id: str,
) -> dict[str, Any]:
    """Delete a Gmail filter by ID."""
    try:
        _, meta = await client.execute_api(
            "settings.filters.delete",
            {"id": filter_id},
            tool_name="gmail_delete_filter",
            op_class="write",
        )
        return {
            "success": True,
            "operation": "gmail_delete_filter",
            "mutates_mailbox": True,
            "filter_id": filter_id,
            "deleted": True,
            "quota_estimate": {
                "units_per_request": 5,
                "total_units": int(meta.get("quota_estimate", 5)),
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_delete_filter", exc, mutates_mailbox=True)
