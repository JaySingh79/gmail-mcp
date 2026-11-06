"""Incremental mailbox synchronization tools using Gmail History API and Google Cloud Pub/Sub."""

from __future__ import annotations

from typing import Any, Literal

from gmail_mcp.core.gmail_client import GmailClientWrapper
from gmail_mcp.core.pagination import collect_paginated_items
from gmail_mcp.core.retry_manager import GmailMCPError
from gmail_mcp.core.safety import SafetyError


async def gmail_start_watch(
    client: GmailClientWrapper,
    *,
    topic_name: str | None = None,
    label_ids: list[str] | None = None,
    label_filter_action: Literal["include", "exclude"] = "include",
) -> dict[str, Any]:
    """
    Start or renew a Gmail push notification watch (`users.watch`) on a configured Google Cloud Pub/Sub topic.
    Disabled unless Pub/Sub topic is explicitly configured and authorized.
    """
    try:
        effective_topic = (topic_name or client.config.pubsub_topic or "").strip()
        if not effective_topic:
            return {
                "success": False,
                "operation": "gmail_start_watch",
                "mutates_mailbox": False,
                "error_code": "PUBSUB_NOT_CONFIGURED",
                "message": (
                    "Push notification watch is disabled because no Google Cloud Pub/Sub topic is configured. "
                    "Set `GMAIL_PUBSUB_TOPIC=projects/<project-id>/topics/<topic>` or pass `topic_name`."
                ),
                "http_status": 400,
                "retryable": False,
            }

        if not effective_topic.startswith("projects/") or "/topics/" not in effective_topic:
            raise SafetyError(
                "INVALID_PUBSUB_TOPIC",
                f"Pub/Sub topic must match 'projects/<project>/topics/<topic>', got '{effective_topic}'.",
            )

        body: dict[str, Any] = {
            "topicName": effective_topic,
            "labelFilterAction": label_filter_action,
        }
        if label_ids:
            body["labelIds"] = label_ids

        resp, meta = await client.execute_api(
            "watch",
            {"body": body},
            tool_name="gmail_start_watch",
            op_class="write",
        )
        history_id = str(resp.get("historyId") or "")
        expiration = str(resp.get("expiration") or "")

        state = client.sync_state_repo.upsert_state(
            history_id=history_id,
            watch_expiration=expiration,
            topic_name=effective_topic,
        )
        return {
            "success": True,
            "operation": "gmail_start_watch",
            "mutates_mailbox": True,
            "history_id": history_id,
            "watch_expiration_ms": expiration,
            "topic_name": effective_topic,
            "sync_state": state.model_dump(),
            "quota_estimate": {
                "units_per_request": 100,
                "total_units": int(meta.get("quota_estimate", 100)),
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_start_watch", exc, mutates_mailbox=True)


async def gmail_stop_watch(client: GmailClientWrapper) -> dict[str, Any]:
    """Stop active Gmail push notifications (`users.stop`)."""
    try:
        _, meta = await client.execute_api(
            "stop",
            {},
            tool_name="gmail_stop_watch",
            op_class="write",
        )
        existing = client.sync_state_repo.get_state()
        if existing:
            client.sync_state_repo.upsert_state(
                history_id=existing.history_id,
                watch_expiration="",
                topic_name="",
            )
        return {
            "success": True,
            "operation": "gmail_stop_watch",
            "mutates_mailbox": True,
            "stopped": True,
            "quota_estimate": {
                "units_per_request": 50,
                "total_units": int(meta.get("quota_estimate", 50)),
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_stop_watch", exc, mutates_mailbox=True)


async def gmail_sync_history(
    client: GmailClientWrapper,
    *,
    start_history_id: str | None = None,
    notification_id: str | None = None,
    label_id: str | None = None,
    history_types: list[Literal["messageAdded", "messageDeleted", "labelAdded", "labelRemoved"]] | None = None,
    max_results: int = 500,
    fallback_query_on_expired: str = "newer_than:7d",
) -> dict[str, Any]:
    """
    Incrementally synchronize mailbox events since `start_history_id` (or the stored SQLite cursor).
    - Deduplicates repeated Pub/Sub `notification_id` events.
    - Persists the latest `history_id`.
    - Automatically falls back to a full resynchronization if the stored `history_id` has expired (`404 Not Found`).
    """
    try:
        # 1. Deduplicate repeated Pub/Sub push notifications
        if notification_id:
            if client.sync_state_repo.is_notification_processed(notification_id):
                stored = client.sync_state_repo.get_state()
                return {
                    "success": True,
                    "operation": "gmail_sync_history",
                    "mutates_mailbox": False,
                    "deduplicated_notification": True,
                    "notification_id": notification_id,
                    "latest_history_id": stored.history_id if stored else start_history_id,
                    "history_records": [],
                    "quota_estimate": {"units_per_request": 0, "total_units": 0},
                }

        stored_state = client.sync_state_repo.get_state()
        cursor_id = (start_history_id or (stored_state.history_id if stored_state else "")).strip()

        total_units = 0

        # If no history cursor exists yet, bootstrap from users.getProfile
        if not cursor_id:
            prof, p_meta = await client.get_profile(tool_name="gmail_sync_history")
            total_units += int(p_meta.get("quota_estimate", 1))
            cursor_id = str(prof.get("historyId") or "")
            state = client.sync_state_repo.upsert_state(
                history_id=cursor_id,
                email_address=prof.get("emailAddress"),
            )
            if notification_id:
                client.sync_state_repo.mark_notification_processed(notification_id, cursor_id)
            return {
                "success": True,
                "operation": "gmail_sync_history",
                "mutates_mailbox": False,
                "bootstrapped_initial_cursor": True,
                "latest_history_id": cursor_id,
                "history_records": [],
                "sync_state": state.model_dump(),
                "quota_estimate": {"units_per_request": 1, "total_units": total_units},
            }

        latest_history_id = cursor_id
        try:
            async def _fetch_page(next_tok: str | None, page_sz: int) -> dict[str, Any]:
                nonlocal total_units, latest_history_id
                params: dict[str, Any] = {
                    "startHistoryId": cursor_id,
                    "maxResults": page_sz,
                }
                if label_id:
                    params["labelId"] = label_id
                if history_types:
                    params["historyTypes"] = history_types
                if next_tok:
                    params["pageToken"] = next_tok
                resp, meta = await client.execute_api(
                    "history.list",
                    params,
                    tool_name="gmail_sync_history",
                    op_class="read",
                )
                total_units += int(meta.get("quota_estimate", 2))
                if resp.get("historyId"):
                    latest_history_id = str(resp["historyId"])
                return resp

            page_data = await collect_paginated_items(
                _fetch_page,
                items_key="history",
                page_size=min(500, max(1, max_results)),
                max_results=max_results,
                auto_paginate_all=True,
            )

            records = page_data["results"]
            messages_added: list[dict[str, Any]] = []
            messages_deleted: list[dict[str, Any]] = []
            labels_added: list[dict[str, Any]] = []
            labels_removed: list[dict[str, Any]] = []

            for rec in records:
                if rec.get("id") and str(rec["id"]) > latest_history_id:
                    latest_history_id = str(rec["id"])
                messages_added.extend(rec.get("messagesAdded") or [])
                messages_deleted.extend(rec.get("messagesDeleted") or [])
                labels_added.extend(rec.get("labelsAdded") or [])
                labels_removed.extend(rec.get("labelsRemoved") or [])

            updated_state = client.sync_state_repo.upsert_state(history_id=latest_history_id)
            if notification_id:
                client.sync_state_repo.mark_notification_processed(notification_id, latest_history_id)

            return {
                "success": True,
                "operation": "gmail_sync_history",
                "mutates_mailbox": False,
                "full_resync_triggered": False,
                "start_history_id": cursor_id,
                "latest_history_id": latest_history_id,
                "history_record_count": len(records),
                "summary": {
                    "messages_added": len(messages_added),
                    "messages_deleted": len(messages_deleted),
                    "labels_added": len(labels_added),
                    "labels_removed": len(labels_removed),
                },
                "history_records": records,
                "sync_state": updated_state.model_dump(),
                "quota_estimate": {
                    "units_per_request": 2,
                    "total_units": total_units,
                },
            }

        except GmailMCPError as api_exc:
            # Handle expired historyId (HTTP 404) through a full resynchronization
            if api_exc.http_status != 404:
                raise

            prof, p_meta = await client.get_profile(tool_name="gmail_sync_history")
            total_units += int(p_meta.get("quota_estimate", 1))
            fresh_history_id = str(prof.get("historyId") or "")

            list_resp, l_meta = await client.execute_api(
                "messages.list",
                {"q": fallback_query_on_expired, "maxResults": min(500, max_results)},
                tool_name="gmail_sync_history",
                op_class="read",
            )
            total_units += int(l_meta.get("quota_estimate", 5))
            resynced_messages = list_resp.get("messages") or []

            updated_state = client.sync_state_repo.upsert_state(
                history_id=fresh_history_id,
                email_address=prof.get("emailAddress"),
            )
            if notification_id:
                client.sync_state_repo.mark_notification_processed(notification_id, fresh_history_id)

            return {
                "success": True,
                "operation": "gmail_sync_history",
                "mutates_mailbox": False,
                "full_resync_triggered": True,
                "expired_history_id": cursor_id,
                "latest_history_id": fresh_history_id,
                "resynced_message_count": len(resynced_messages),
                "resynced_messages": resynced_messages,
                "sync_state": updated_state.model_dump(),
                "quota_estimate": {
                    "units_per_request": 6,
                    "total_units": total_units,
                },
            }
    except Exception as exc:
        return client.format_tool_error("gmail_sync_history", exc, mutates_mailbox=False)
