"""Account, authentication status, runtime limits, health check, and mailbox statistics tools."""

from __future__ import annotations

import asyncio
from typing import Any

from gmail_mcp.core.gmail_client import GmailClientWrapper

SYSTEM_STATS_LABELS = [
    "INBOX",
    "UNREAD",
    "SENT",
    "DRAFT",
    "SPAM",
    "TRASH",
    "STARRED",
    "IMPORTANT",
]


async def gmail_get_profile(client: GmailClientWrapper) -> dict[str, Any]:
    """Return authenticated email address, mailbox profile, and current history ID."""
    try:
        profile, meta = await client.get_profile(tool_name="gmail_get_profile")
        return {
            "success": True,
            "operation": "gmail_get_profile",
            "mutates_mailbox": False,
            "email_address": profile.get("emailAddress"),
            "messages_total": int(profile.get("messagesTotal") or 0),
            "threads_total": int(profile.get("threadsTotal") or 0),
            "history_id": str(profile.get("historyId") or ""),
            "quota_estimate": {
                "units_per_request": 1,
                "total_units": meta.get("quota_estimate", 1),
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_get_profile", exc, mutates_mailbox=False)


async def gmail_auth_status(
    client: GmailClientWrapper,
    verify_connectivity: bool = True,
) -> dict[str, Any]:
    """Check OAuth token availability, granted scopes, authentication status, and optional API connectivity."""
    try:
        report = client.token_manager.get_auth_status(auto_refresh=True)
        api_connected = False
        authenticated_email: str | None = None
        history_id: str | None = None
        warnings: list[str] = []

        if report.missing_default_scopes:
            warnings.append(
                f"Missing recommended OAuth scopes: {', '.join(report.missing_default_scopes)}"
            )

        if report.authenticated and verify_connectivity:
            try:
                profile, _ = await client.get_profile(tool_name="gmail_auth_status")
                api_connected = True
                authenticated_email = profile.get("emailAddress")
                history_id = str(profile.get("historyId") or "")
            except Exception as exc:
                api_connected = False
                warnings.append(f"API connectivity verification failed: {exc}")

        limits = client.quota_manager.get_runtime_limits_report()
        warnings.extend(limits["locally_measured_usage"]["sending_headroom"].get("warnings", []))

        return {
            "success": True,
            "operation": "gmail_auth_status",
            "mutates_mailbox": False,
            "authenticated": report.authenticated,
            "authenticated_account": authenticated_email,
            "history_id": history_id,
            "api_connectivity": api_connected,
            "status_code": report.status_code,
            "message": report.message,
            "token_file_exists": report.token_file_exists,
            "credentials_file_exists": report.credentials_file_exists,
            "token_expired": report.token_expired,
            "has_refresh_token": report.has_refresh_token,
            "expiry_utc": report.expiry_utc,
            "granted_scopes": report.granted_scopes,
            "missing_default_scopes": report.missing_default_scopes,
            "reauthorization_steps": report.reauthorization_steps,
            "configured_limits_summary": limits["locally_enforced_safeguards"],
            "quota_model_verification": limits["cloud_project"],
            "warnings": warnings,
            "quota_estimate": {
                "units_per_request": 1 if (report.authenticated and verify_connectivity) else 0,
                "total_units": 1 if (report.authenticated and verify_connectivity) else 0,
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_auth_status", exc, mutates_mailbox=False)


async def gmail_get_runtime_limits(
    client: GmailClientWrapper,
    reconcile_sent_mail: bool = False,
) -> dict[str, Any]:
    """Report configured account limits, quota model, locally measured usage, and operational headroom."""
    try:
        reconciled_units = 0
        if reconcile_sent_mail:
            res = await client.reconcile_sent_mail_24h(tool_name="gmail_get_runtime_limits")
            if res is not None:
                reconciled_units = 5

        report = client.quota_manager.get_runtime_limits_report()
        return {
            "success": True,
            "operation": "gmail_get_runtime_limits",
            "mutates_mailbox": False,
            "runtime_limits": report,
            "concurrency_state": client.rate_limiter.concurrency_metrics,
            "quota_estimate": {
                "units_per_request": reconciled_units,
                "total_units": reconciled_units,
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_get_runtime_limits", exc, mutates_mailbox=False)


async def gmail_health_check(client: GmailClientWrapper) -> dict[str, Any]:
    """Check Gmail API connectivity, OAuth validity, SQLite database state, and allowed directories."""
    try:
        db_health = client.db.check_health()
        auth_report = client.token_manager.get_auth_status(auto_refresh=True)
        allowed_dirs_info = [
            {"path": str(d), "exists": d.exists(), "is_dir": d.is_dir()}
            for d in client.config.allowed_dirs
        ]

        api_reachable = False
        profile_summary: dict[str, Any] | None = None
        api_error: str | None = None
        units_used = 0

        if auth_report.authenticated or client._custom_dispatcher is not None:
            try:
                prof, meta = await client.get_profile(tool_name="gmail_health_check")
                api_reachable = True
                units_used = int(meta.get("quota_estimate", 1))
                profile_summary = {
                    "emailAddress": prof.get("emailAddress"),
                    "historyId": prof.get("historyId"),
                }
            except Exception as exc:
                api_error = f"{type(exc).__name__}: {exc}"

        overall_healthy = bool(db_health.get("healthy") and (api_reachable or not auth_report.token_file_exists))
        return {
            "success": True,
            "operation": "gmail_health_check",
            "mutates_mailbox": False,
            "healthy": overall_healthy,
            "api_reachable": api_reachable,
            "profile": profile_summary,
            "api_error": api_error,
            "oauth_status": auth_report.status_code,
            "database": db_health,
            "allowed_directories": allowed_dirs_info,
            "quota_estimate": {
                "units_per_request": 1 if api_reachable else 0,
                "total_units": units_used,
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_health_check", exc, mutates_mailbox=False)


async def gmail_get_mailbox_stats(
    client: GmailClientWrapper,
    include_user_labels: bool = True,
) -> dict[str, Any]:
    """
    Retrieve mailbox statistics and exact label-based counts (total, unread, inbox, sent, drafts, spam, trash).
    Note: Profile and label counters come from `users.getProfile` and `labels.get`; search estimates are never misrepresented as exact counts.
    """
    try:
        total_units = 0
        profile, prof_meta = await client.get_profile(tool_name="gmail_get_mailbox_stats")
        total_units += int(prof_meta.get("quota_estimate", 1))

        labels_resp, lbl_meta = await client.execute_api(
            "labels.list", {}, tool_name="gmail_get_mailbox_stats", op_class="read"
        )
        total_units += int(lbl_meta.get("quota_estimate", 1))
        all_labels = labels_resp.get("labels") or []

        target_label_ids: list[str] = []
        for lbl in all_labels:
            lid = lbl.get("id")
            ltype = lbl.get("type", "user")
            if lid in SYSTEM_STATS_LABELS or (include_user_labels and ltype == "user"):
                if lid:
                    target_label_ids.append(lid)

        for sys_id in SYSTEM_STATS_LABELS:
            if sys_id not in target_label_ids and any(l.get("id") == sys_id for l in all_labels):
                target_label_ids.append(sys_id)

        async def _fetch_label_detail(lid: str) -> tuple[str, dict[str, Any], int]:
            detail, m = await client.execute_api(
                "labels.get",
                {"id": lid},
                tool_name="gmail_get_mailbox_stats",
                op_class="read",
            )
            return lid, detail, int(m.get("quota_estimate", 1))

        details_results = await asyncio.gather(*[_fetch_label_detail(lid) for lid in target_label_ids])
        system_counts: dict[str, dict[str, int]] = {}
        user_label_stats: list[dict[str, Any]] = []

        for lid, detail, u_cost in details_results:
            total_units += u_cost
            entry = {
                "id": lid,
                "name": detail.get("name", lid),
                "type": detail.get("type", "system" if lid in SYSTEM_STATS_LABELS else "user"),
                "messages_total": int(detail.get("messagesTotal") or 0),
                "messages_unread": int(detail.get("messagesUnread") or 0),
                "threads_total": int(detail.get("threadsTotal") or 0),
                "threads_unread": int(detail.get("threadsUnread") or 0),
            }
            if lid in SYSTEM_STATS_LABELS:
                system_counts[lid] = {
                    "messages_total": entry["messages_total"],
                    "messages_unread": entry["messages_unread"],
                    "threads_total": entry["threads_total"],
                    "threads_unread": entry["threads_unread"],
                }
            else:
                user_label_stats.append(entry)

        return {
            "success": True,
            "operation": "gmail_get_mailbox_stats",
            "mutates_mailbox": False,
            "email_address": profile.get("emailAddress"),
            "mailbox_totals": {
                "messages_total": int(profile.get("messagesTotal") or 0),
                "threads_total": int(profile.get("threadsTotal") or 0),
                "history_id": str(profile.get("historyId") or ""),
                "unread_messages": system_counts.get("UNREAD", {}).get(
                    "messages_total",
                    system_counts.get("INBOX", {}).get("messages_unread", 0),
                ),
                "inbox_messages": system_counts.get("INBOX", {}).get("messages_total", 0),
                "inbox_unread": system_counts.get("INBOX", {}).get("messages_unread", 0),
                "sent_messages": system_counts.get("SENT", {}).get("messages_total", 0),
                "draft_count": system_counts.get("DRAFT", {}).get("messages_total", 0),
                "spam_count": system_counts.get("SPAM", {}).get("messages_total", 0),
                "trash_count": system_counts.get("TRASH", {}).get("messages_total", 0),
            },
            "system_labels": system_counts,
            "user_labels": user_label_stats,
            "accuracy_note": (
                "Counts are retrieved from Gmail's authoritative `users.getProfile` and `labels.get` endpoints, "
                "not from `resultSizeEstimate` search approximations."
            ),
            "quota_estimate": {
                "units_per_request": 1,
                "total_units": total_units,
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_get_mailbox_stats", exc, mutates_mailbox=False)
