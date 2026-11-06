"""Gmail label management tools (`list`, `create`, `update`, `delete`, `apply`, `remove`, `bulk_label`)."""

from __future__ import annotations

from typing import Any, Literal

from gmail_mcp.core.gmail_client import GmailClientWrapper
from gmail_mcp.core.safety import SafetyError

PROTECTED_SYSTEM_LABELS = {
    "INBOX",
    "SENT",
    "TRASH",
    "SPAM",
    "UNREAD",
    "STARRED",
    "IMPORTANT",
    "DRAFT",
    "CHAT",
    "CATEGORY_PERSONAL",
    "CATEGORY_SOCIAL",
    "CATEGORY_PROMOTIONS",
    "CATEGORY_UPDATES",
    "CATEGORY_FORUMS",
}


async def _resolve_label_ids(
    client: GmailClientWrapper,
    label_ids: list[str] | str | None,
    label_names: list[str] | str | None,
    tool_name: str,
) -> tuple[list[str], int]:
    """Resolve a mix of label IDs and human-readable label names into canonical Gmail label IDs."""
    ids_out: list[str] = []
    units_used = 0

    if isinstance(label_ids, str):
        ids_out.append(label_ids.strip())
    elif label_ids:
        ids_out.extend(x.strip() for x in label_ids if x and x.strip())

    names_in: list[str] = []
    if isinstance(label_names, str):
        names_in.append(label_names.strip())
    elif label_names:
        names_in.extend(x.strip() for x in label_names if x and x.strip())

    if names_in:
        resp, meta = await client.execute_api("labels.list", {}, tool_name=tool_name, op_class="read")
        units_used += int(meta.get("quota_estimate", 1))
        labels = resp.get("labels") or []
        by_name = {(l.get("name") or "").lower(): l.get("id") for l in labels if l.get("id")}
        by_id = {(l.get("id") or "").lower(): l.get("id") for l in labels if l.get("id")}

        for name in names_in:
            lower = name.lower()
            if lower in by_name:
                ids_out.append(by_name[lower])
            elif lower in by_id:
                ids_out.append(by_id[lower])
            else:
                raise SafetyError(
                    "LABEL_NOT_FOUND",
                    f"Label '{name}' was not found in the mailbox.",
                    {"label_name": name},
                )

    deduped = list(dict.fromkeys(x for x in ids_out if x))
    if not deduped:
        raise SafetyError("MISSING_LABELS", "Provide at least one `label_id` / `label_ids` or `label_name` / `label_names`.")
    return deduped, units_used


async def gmail_list_labels(client: GmailClientWrapper) -> dict[str, Any]:
    """List all system and user-created Gmail labels."""
    try:
        resp, meta = await client.execute_api(
            "labels.list", {}, tool_name="gmail_list_labels", op_class="read"
        )
        labels = resp.get("labels") or []
        return {
            "success": True,
            "operation": "gmail_list_labels",
            "mutates_mailbox": False,
            "count": len(labels),
            "labels": labels,
            "quota_estimate": {
                "units_per_request": 1,
                "total_units": int(meta.get("quota_estimate", 1)),
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_list_labels", exc, mutates_mailbox=False)


async def gmail_create_label(
    client: GmailClientWrapper,
    label_name: str,
    *,
    label_list_visibility: Literal["labelShow", "labelShowIfUnread", "labelHide"] = "labelShow",
    message_list_visibility: Literal["show", "hide"] = "show",
    background_color: str | None = None,
    text_color: str | None = None,
) -> dict[str, Any]:
    """Create a user label (supports nested label names such as `'Projects/Active'`)."""
    try:
        clean_name = (label_name or "").strip()
        if not clean_name:
            raise SafetyError("INVALID_LABEL_NAME", "`label_name` cannot be empty.")
        if clean_name.upper() in PROTECTED_SYSTEM_LABELS:
            raise SafetyError("RESERVED_SYSTEM_LABEL", f"Cannot create reserved system label '{clean_name}'.")

        body: dict[str, Any] = {
            "name": clean_name,
            "labelListVisibility": label_list_visibility,
            "messageListVisibility": message_list_visibility,
        }
        if background_color or text_color:
            body["color"] = {}
            if background_color:
                body["color"]["backgroundColor"] = background_color
            if text_color:
                body["color"]["textColor"] = text_color

        resp, meta = await client.execute_api(
            "labels.create",
            {"body": body},
            tool_name="gmail_create_label",
            op_class="write",
        )
        client.invalidate_metadata_cache()
        return {
            "success": True,
            "operation": "gmail_create_label",
            "mutates_mailbox": True,
            "label": resp,
            "label_id": resp.get("id"),
            "label_name": resp.get("name") or clean_name,
            "quota_estimate": {
                "units_per_request": 5,
                "total_units": int(meta.get("quota_estimate", 5)),
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_create_label", exc, mutates_mailbox=True)


async def gmail_update_label(
    client: GmailClientWrapper,
    label_id: str,
    *,
    new_name: str | None = None,
    label_list_visibility: Literal["labelShow", "labelShowIfUnread", "labelHide"] | None = None,
    message_list_visibility: Literal["show", "hide"] | None = None,
    background_color: str | None = None,
    text_color: str | None = None,
) -> dict[str, Any]:
    """Modify properties of an existing user-created label."""
    try:
        if label_id.upper() in PROTECTED_SYSTEM_LABELS:
            raise SafetyError("PROTECTED_SYSTEM_LABEL", f"System label '{label_id}' cannot be modified.")

        body: dict[str, Any] = {"id": label_id}
        if new_name is not None:
            body["name"] = new_name.strip()
        if label_list_visibility is not None:
            body["labelListVisibility"] = label_list_visibility
        if message_list_visibility is not None:
            body["messageListVisibility"] = message_list_visibility
        if background_color or text_color:
            body["color"] = {}
            if background_color:
                body["color"]["backgroundColor"] = background_color
            if text_color:
                body["color"]["textColor"] = text_color

        resp, meta = await client.execute_api(
            "labels.update",
            {"id": label_id, "body": body},
            tool_name="gmail_update_label",
            op_class="write",
        )
        client.invalidate_metadata_cache()
        return {
            "success": True,
            "operation": "gmail_update_label",
            "mutates_mailbox": True,
            "label": resp,
            "quota_estimate": {
                "units_per_request": 5,
                "total_units": int(meta.get("quota_estimate", 5)),
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_update_label", exc, mutates_mailbox=True)


async def gmail_delete_label(
    client: GmailClientWrapper,
    label_id: str,
) -> dict[str, Any]:
    """Delete a user-created label (system labels are protected and cannot be deleted)."""
    try:
        if label_id.upper() in PROTECTED_SYSTEM_LABELS:
            raise SafetyError("PROTECTED_SYSTEM_LABEL", f"System label '{label_id}' cannot be deleted.")

        _, meta = await client.execute_api(
            "labels.delete",
            {"id": label_id},
            tool_name="gmail_delete_label",
            op_class="write",
        )
        client.invalidate_metadata_cache()
        return {
            "success": True,
            "operation": "gmail_delete_label",
            "mutates_mailbox": True,
            "label_id": label_id,
            "deleted": True,
            "quota_estimate": {
                "units_per_request": 5,
                "total_units": int(meta.get("quota_estimate", 5)),
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_delete_label", exc, mutates_mailbox=True)


async def gmail_apply_label(
    client: GmailClientWrapper,
    *,
    message_ids: list[str] | str | None = None,
    thread_ids: list[str] | str | None = None,
    label_ids: list[str] | str | None = None,
    label_name: list[str] | str | None = None,
) -> dict[str, Any]:
    """
    Apply one or more labels (by ID or name) to messages or threads.
    Note: Native Gmail labels apply to messages/threads; local draft/template tagging is not misrepresented as Gmail labeling.
    """
    try:
        resolved_labels, total_units = await _resolve_label_ids(
            client, label_ids, label_name, "gmail_apply_label"
        )
        msg_list = [message_ids] if isinstance(message_ids, str) else list(message_ids or [])
        th_list = [thread_ids] if isinstance(thread_ids, str) else list(thread_ids or [])

        if not msg_list and not th_list:
            raise SafetyError("MISSING_TARGETS", "Provide `message_ids` and/or `thread_ids`.")

        if len(msg_list) == 1:
            _, meta = await client.execute_api(
                "messages.modify",
                {"id": msg_list[0], "body": {"addLabelIds": resolved_labels}},
                tool_name="gmail_apply_label",
                op_class="write",
            )
            total_units += int(meta.get("quota_estimate", 5))
        elif len(msg_list) > 1:
            for start in range(0, len(msg_list), 1000):
                chunk = msg_list[start : start + 1000]
                _, meta = await client.execute_api(
                    "messages.batchModify",
                    {"body": {"ids": chunk, "addLabelIds": resolved_labels}},
                    tool_name="gmail_apply_label",
                    op_class="write",
                )
                total_units += int(meta.get("quota_estimate", 50))

        for tid in th_list:
            _, meta = await client.execute_api(
                "threads.modify",
                {"id": tid, "body": {"addLabelIds": resolved_labels}},
                tool_name="gmail_apply_label",
                op_class="write",
            )
            total_units += int(meta.get("quota_estimate", 10))

        return {
            "success": True,
            "operation": "gmail_apply_label",
            "mutates_mailbox": True,
            "applied_label_ids": resolved_labels,
            "messages_modified": len(msg_list),
            "threads_modified": len(th_list),
            "quota_estimate": {
                "units_per_request": 50 if len(msg_list) > 1 else 5,
                "total_units": total_units,
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_apply_label", exc, mutates_mailbox=True)


async def gmail_remove_label(
    client: GmailClientWrapper,
    *,
    message_ids: list[str] | str | None = None,
    thread_ids: list[str] | str | None = None,
    label_ids: list[str] | str | None = None,
    label_name: list[str] | str | None = None,
) -> dict[str, Any]:
    """Remove one or more labels (by ID or name) from messages or threads."""
    try:
        resolved_labels, total_units = await _resolve_label_ids(
            client, label_ids, label_name, "gmail_remove_label"
        )
        msg_list = [message_ids] if isinstance(message_ids, str) else list(message_ids or [])
        th_list = [thread_ids] if isinstance(thread_ids, str) else list(thread_ids or [])

        if not msg_list and not th_list:
            raise SafetyError("MISSING_TARGETS", "Provide `message_ids` and/or `thread_ids`.")

        if len(msg_list) == 1:
            _, meta = await client.execute_api(
                "messages.modify",
                {"id": msg_list[0], "body": {"removeLabelIds": resolved_labels}},
                tool_name="gmail_remove_label",
                op_class="write",
            )
            total_units += int(meta.get("quota_estimate", 5))
        elif len(msg_list) > 1:
            for start in range(0, len(msg_list), 1000):
                chunk = msg_list[start : start + 1000]
                _, meta = await client.execute_api(
                    "messages.batchModify",
                    {"body": {"ids": chunk, "removeLabelIds": resolved_labels}},
                    tool_name="gmail_remove_label",
                    op_class="write",
                )
                total_units += int(meta.get("quota_estimate", 50))

        for tid in th_list:
            _, meta = await client.execute_api(
                "threads.modify",
                {"id": tid, "body": {"removeLabelIds": resolved_labels}},
                tool_name="gmail_remove_label",
                op_class="write",
            )
            total_units += int(meta.get("quota_estimate", 10))

        return {
            "success": True,
            "operation": "gmail_remove_label",
            "mutates_mailbox": True,
            "removed_label_ids": resolved_labels,
            "messages_modified": len(msg_list),
            "threads_modified": len(th_list),
            "quota_estimate": {
                "units_per_request": 50 if len(msg_list) > 1 else 5,
                "total_units": total_units,
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_remove_label", exc, mutates_mailbox=True)


async def gmail_bulk_label_messages(
    client: GmailClientWrapper,
    message_ids: list[str],
    *,
    add_label_ids: list[str] | str | None = None,
    add_label_names: list[str] | str | None = None,
    remove_label_ids: list[str] | str | None = None,
    remove_label_names: list[str] | str | None = None,
    chunk_size: int = 1000,
) -> dict[str, Any]:
    """
    Apply and/or remove labels across 100 to 1,000+ messages using Gmail's dedicated `messages.batchModify` endpoint.
    """
    try:
        if not message_ids:
            raise SafetyError("EMPTY_MESSAGE_LIST", "`message_ids` cannot be empty.")

        total_units = 0
        to_add: list[str] = []
        to_remove: list[str] = []

        if add_label_ids or add_label_names:
            to_add, u1 = await _resolve_label_ids(
                client, add_label_ids, add_label_names, "gmail_bulk_label_messages"
            )
            total_units += u1

        if remove_label_ids or remove_label_names:
            to_remove, u2 = await _resolve_label_ids(
                client, remove_label_ids, remove_label_names, "gmail_bulk_label_messages"
            )
            total_units += u2

        if not to_add and not to_remove:
            raise SafetyError(
                "MISSING_LABELS",
                "Provide at least one label to add (`add_label_ids`/`add_label_names`) or remove (`remove_label_ids`/`remove_label_names`).",
            )

        eff_chunk = max(1, min(1000, int(chunk_size)))
        modified_count = 0
        failed_chunks: list[dict[str, Any]] = []
        batches_executed = 0

        for start in range(0, len(message_ids), eff_chunk):
            chunk = message_ids[start : start + eff_chunk]
            body: dict[str, Any] = {"ids": chunk}
            if to_add:
                body["addLabelIds"] = to_add
            if to_remove:
                body["removeLabelIds"] = to_remove
            try:
                _, meta = await client.execute_api(
                    "messages.batchModify",
                    {"body": body},
                    tool_name="gmail_bulk_label_messages",
                    op_class="write",
                )
                total_units += int(meta.get("quota_estimate", 50))
                modified_count += len(chunk)
                batches_executed += 1
            except Exception as chunk_exc:
                err = client.format_tool_error("gmail_bulk_label_messages", chunk_exc, mutates_mailbox=True)
                failed_chunks.append(
                    {
                        "chunk_start": start,
                        "chunk_size": len(chunk),
                        "error_code": err["error_code"],
                        "message": err["message"],
                        "retryable": err["retryable"],
                    }
                )

        requested = len(message_ids)
        failed_count = requested - modified_count
        return {
            "success": (modified_count == requested and len(failed_chunks) == 0),
            "operation": "bulk_label_messages",
            "mutates_mailbox": modified_count > 0,
            "requested": requested,
            "successful": modified_count,
            "modified": modified_count,
            "failed": failed_count,
            "skipped": 0,
            "batches_executed": batches_executed,
            "added_label_ids": to_add,
            "removed_label_ids": to_remove,
            "retryable_failures": [f for f in failed_chunks if f.get("retryable")],
            "non_retryable_failures": [f for f in failed_chunks if not f.get("retryable")],
            "safe_to_continue": len(failed_chunks) == 0,
            "quota_estimate": {
                "units_per_request": 50,
                "total_units": total_units,
            },
        }
    except Exception as exc:
        return client.format_tool_error("bulk_label_messages", exc, mutates_mailbox=True)
