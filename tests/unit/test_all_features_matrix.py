"""Feature-by-Feature Test Matrix (Section 12 of gmail_mcp_server.md)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from gmail_mcp.core.gmail_client import GmailClientWrapper
from gmail_mcp.storage.database import DatabaseManager
from tests.conftest import MockGmailBackend, make_http_error
from gmail_mcp.tools import (
    gmail_apply_label,
    gmail_auth_status,
    gmail_cancel_scheduled_send,
    gmail_create_draft,
    gmail_create_filter,
    gmail_create_label,
    gmail_create_template,
    gmail_delete_draft,
    gmail_delete_filter,
    gmail_delete_label,
    gmail_delete_template,
    gmail_duplicate_draft,
    gmail_export_messages,
    gmail_forward_message,
    gmail_get_attachment,
    gmail_get_draft,
    gmail_get_filter,
    gmail_get_mailbox_settings,
    gmail_get_mailbox_stats,
    gmail_get_message,
    gmail_get_profile,
    gmail_get_runtime_limits,
    gmail_get_template,
    gmail_get_thread,
    gmail_health_check,
    gmail_list_attachments,
    gmail_list_drafts,
    gmail_list_filters,
    gmail_list_labels,
    gmail_list_scheduled_sends,
    gmail_list_templates,
    gmail_list_unread,
    gmail_modify_messages,
    gmail_preview_template,
    gmail_process_due_scheduled_sends,
    gmail_remove_label,
    gmail_schedule_send,
    gmail_search_messages,
    gmail_search_threads,
    gmail_send_email,
    gmail_send_reply,
    gmail_send_reply_all,
    gmail_start_watch,
    gmail_stop_watch,
    gmail_sync_history,
    gmail_update_draft,
    gmail_update_label,
    gmail_update_template,
    gmail_update_vacation_settings,
)


@pytest.mark.asyncio()
async def test_account_diagnostics_and_statistics(
    gmail_client: GmailClientWrapper,
    mock_backend: MockGmailBackend,
) -> None:
    """Verify profile, auth status, runtime limits, health check, and mailbox stats (including empty mailbox)."""
    # Empty mailbox stats
    empty_stats = await gmail_get_mailbox_stats(gmail_client)
    assert empty_stats["success"] is True
    assert empty_stats["mailbox_totals"]["messages_total"] == 0

    # Seed messages and verify counts
    mock_backend.seed_message(msg_id="m1", label_ids=["INBOX", "UNREAD"])
    mock_backend.seed_message(msg_id="m2", label_ids=["INBOX"])
    mock_backend.seed_message(msg_id="m3", label_ids=["SENT"])

    prof = await gmail_get_profile(gmail_client)
    assert prof["success"] is True
    assert prof["email_address"] == "testuser@gmail.com"
    assert prof["messages_total"] == 3

    auth = await gmail_auth_status(gmail_client)
    assert auth["success"] is True
    assert auth["authenticated"] is True
    assert auth["api_connectivity"] is True

    limits = await gmail_get_runtime_limits(gmail_client, reconcile_sent_mail=True)
    assert limits["success"] is True
    assert limits["runtime_limits"]["cloud_project"]["project_number"] in ("542762227233", "187614359153")

    health = await gmail_health_check(gmail_client)
    assert health["success"] is True
    assert health["healthy"] is True

    stats = await gmail_get_mailbox_stats(gmail_client)
    assert stats["success"] is True
    assert stats["mailbox_totals"]["inbox_messages"] == 2
    assert stats["mailbox_totals"]["inbox_unread"] == 1
    assert stats["mailbox_totals"]["sent_messages"] == 1


@pytest.mark.asyncio()
async def test_search_low_latency_metadata_full_email_and_threads(
    gmail_client: GmailClientWrapper,
    mock_backend: MockGmailBackend,
    temp_workspace: dict[str, Path],
) -> None:
    """Test search conditions, zero results, metadata vs full retrieval, threads, and attachment download."""
    mock_backend.seed_message(
        msg_id="m_inv",
        thread_id="th_inv",
        sender="recruiter@acme.com",
        subject="Interview Invitation",
        body_text="Plaintext interview details",
        body_html="<div>HTML interview details</div>",
        label_ids=["INBOX", "UNREAD"],
        attachments=[("invite.pdf", "application/pdf", b"%PDF-1.4 invite")],
    )
    mock_backend.seed_message(
        msg_id="m_inv_2",
        thread_id="th_inv",
        sender="testuser@gmail.com",
        subject="Re: Interview Invitation",
        body_text="Follow-up in same thread",
        label_ids=["SENT"],
    )

    # 1. Exact sender + subject + has:attachment in metadata format (low-latency: no bodies returned)
    res_meta = await gmail_search_messages(
        gmail_client,
        query='from:recruiter@acme.com subject:"Interview Invitation" has:attachment',
        format="metadata",
    )
    assert res_meta["success"] is True
    assert res_meta["returned_count"] == 1
    item = res_meta["results"][0]
    assert item["id"] == "m_inv"
    assert "body_text" not in item
    assert "body_html" not in item
    assert item["is_unread"] is True
    # Must preserve UNREAD status on mock_backend
    assert "UNREAD" in mock_backend.messages["m_inv"]["labelIds"]

    # 2. Zero results
    res_zero = await gmail_search_messages(gmail_client, query="from:nobody@nowhere.invalid")
    assert res_zero["success"] is True
    assert res_zero["returned_count"] == 0
    assert res_zero["results"] == []

    # 3. Full email retrieval decodes plaintext and HTML
    full_msg = await gmail_get_message(gmail_client, message_id="m_inv", format="full")
    assert full_msg["success"] is True
    assert full_msg["message"]["body_text"] == "Plaintext interview details"
    assert full_msg["message"]["body_html"] == "<div>HTML interview details</div>"

    # 4. Thread retrieval with multiple messages + stale/missing thread 404
    th_res = await gmail_get_thread(gmail_client, thread_id="th_inv", format="full")
    assert th_res["success"] is True
    assert th_res["message_count"] == 2

    th_missing = await gmail_get_thread(gmail_client, thread_id="th_stale_404")
    assert th_missing["success"] is False
    assert th_missing["error_code"] == "RESOURCE_NOT_FOUND"

    th_search = await gmail_search_threads(gmail_client, query="from:recruiter@acme.com")
    assert th_search["success"] is True
    assert th_search["returned_count"] == 1

    # 5. List attachments & download attachment
    att_list = await gmail_list_attachments(gmail_client, message_id="m_inv")
    assert att_list["success"] is True
    assert att_list["attachment_count"] == 1
    att_id = att_list["attachments"][0]["attachment_id"]

    dl_res = await gmail_get_attachment(
        gmail_client,
        message_id="m_inv",
        attachment_id=att_id,
        destination_directory=str(temp_workspace["downloads"]),
    )
    assert dl_res["success"] is True
    assert Path(dl_res["saved_path"]).read_bytes() == b"%PDF-1.4 invite"


@pytest.mark.asyncio()
async def test_draft_lifecycle_duplication_and_update_preserving_attachments(
    gmail_client: GmailClientWrapper,
    temp_workspace: dict[str, Path],
) -> None:
    """Test draft create, get, list, 10x duplication preserving content/attachments, update preserving attachments, and delete."""
    pdf1 = str(temp_workspace["resume_pdf"])
    pdf2 = str(temp_workspace["portfolio_pdf"])

    created = await gmail_create_draft(
        gmail_client,
        to="initial@example.com",
        subject="Original Draft Subject",
        body_text="Original plaintext",
        body_html="<p>Original HTML</p>",
        attachments=[pdf1],
    )
    assert created["success"] is True
    did = created["draft_id"]

    # Duplicate 10 copies preserving original content and attachments
    Overrides = [f"user{i}@example.com" for i in range(10)]
    dup = await gmail_duplicate_draft(
        gmail_client,
        source_draft_id=did,
        copies=10,
        recipient_overrides=Overrides,
    )
    assert dup["success"] is True
    assert dup["created"] == 10
    dup_ids = [d["draft_id"] for d in dup["drafts"]]
    assert did not in dup_ids
    assert len(set(dup_ids)) == 10

    # Verify duplicated draft preserved attachment and body
    fetched_dup = await gmail_get_draft(gmail_client, draft_id=dup_ids[0], format="raw")
    assert fetched_dup["success"] is True
    assert fetched_dup["draft"]["subject"] == "Original Draft Subject"
    assert fetched_dup["draft"]["to"] == ["user0@example.com"]
    assert len(fetched_dup["draft"]["attachments"]) == 1
    assert fetched_dup["draft"]["attachments"][0]["filename"] == "resume.pdf"

    # Update original draft adding a second attachment while preserving existing resume.pdf
    updated = await gmail_update_draft(
        gmail_client,
        draft_id=did,
        subject="Updated Draft Subject",
        additional_attachments=[pdf2],
        preserve_existing_attachments=True,
    )
    assert updated["success"] is True
    assert len(updated["attachments"]) == 2
    filenames = {a["filename"] for a in updated["attachments"]}
    assert filenames == {"resume.pdf", "portfolio.pdf"}

    listed = await gmail_list_drafts(gmail_client)
    assert listed["success"] is True
    assert listed["returned_count"] == 11

    deleted = await gmail_delete_draft(gmail_client, draft_id=dup_ids[-1])
    assert deleted["success"] is True


@pytest.mark.asyncio()
async def test_single_send_duplicate_protection_reply_and_forward(
    gmail_client: GmailClientWrapper,
    mock_backend: MockGmailBackend,
) -> None:
    """Test single send, invalid recipient, idempotency duplicate protection, threaded reply RFC headers, and forward."""
    # 1. Invalid recipient rejected
    bad_send = await gmail_send_email(
        gmail_client,
        to="invalid-email",
        subject="Test",
        body_text="Body",
        confirm_send=True,
    )
    assert bad_send["success"] is False
    assert bad_send["error_code"] == "INVALID_RECIPIENT"

    # 2. Successful send + repeat same request -> deduplicated without resending
    send1 = await gmail_send_email(
        gmail_client,
        to="hiring@company.com",
        subject="Application",
        body_text="Hello team",
        confirm_send=True,
    )
    assert send1["success"] is True
    assert send1["deduplicated"] is False
    msg_id_1 = send1["message_id"]

    send2 = await gmail_send_email(
        gmail_client,
        to="hiring@company.com",
        subject="Application",
        body_text="Hello team",
        confirm_send=True,
    )
    assert send2["success"] is True
    assert send2["deduplicated"] is True
    assert send2["message_id"] == msg_id_1

    # 3. Reply preserves thread ID and sets In-Reply-To & References headers
    seeded = mock_backend.seed_message(
        msg_id="m_thread_orig",
        thread_id="th_conv_99",
        sender="founder@startup.io",
        to="testuser@gmail.com",
        cc="cto@startup.io",
        subject="Technical Discussion",
        rfc_message_id="<orig99@startup.io>",
        references="<root00@startup.io>",
    )
    reply_res = await gmail_send_reply(
        gmail_client,
        thread_id="th_conv_99",
        message_id=seeded["id"],
        body_text="Thanks for reaching out!",
        confirm_send=True,
    )
    assert reply_res["success"] is True
    assert reply_res["thread_id"] == "th_conv_99"
    assert reply_res["in_reply_to"] == "<orig99@startup.io>"
    assert "<root00@startup.io>" in reply_res["references"]
    assert "<orig99@startup.io>" in reply_res["references"]
    assert reply_res["subject"] == "Re: Technical Discussion"

    # 4. Reply-All includes original CC but excludes authenticated user
    reply_all_res = await gmail_send_reply_all(
        gmail_client,
        thread_id="th_conv_99",
        message_id=seeded["id"],
        body_text="Replying to everyone.",
        confirm_send=True,
    )
    assert reply_all_res["success"] is True
    assert "cto@startup.io" in reply_all_res["recipients"]["cc"]
    assert "testuser@gmail.com" not in reply_all_res["recipients"]["cc"]

    # 5. Forward preserves original content and attachments
    seeded_att = mock_backend.seed_message(
        msg_id="m_to_fwd",
        sender="partner@org.com",
        subject="Contract PDF",
        body_text="Please see attached contract.",
        attachments=[("contract.pdf", "application/pdf", b"%PDF-1.4 contract")],
    )
    fwd_res = await gmail_forward_message(
        gmail_client,
        message_id=seeded_att["id"],
        to="colleague@company.com",
        comment_text="FYI see below.",
        include_original_attachments=True,
        confirm_send=True,
    )
    assert fwd_res["success"] is True
    assert fwd_res["subject"] == "Fwd: Contract PDF"
    assert len(fwd_res["attachments"]) == 1
    assert fwd_res["attachments"][0]["filename"] == "contract.pdf"


@pytest.mark.asyncio()
async def test_templates_labels_mailbox_actions_filters_and_settings(
    gmail_client: GmailClientWrapper,
    mock_backend: MockGmailBackend,
) -> None:
    """Test template CRUD, label CRUD + apply/remove, mailbox modify actions, filters CRUD, and vacation settings."""
    # Templates
    tpl_c = await gmail_create_template(
        gmail_client,
        name="Job Application",
        subject="Application for {{position}}",
        body_text="Hello {{name}}, applying for {{position}}.",
        body_html="<p>Hello {{name}}, applying for <b>{{position}}</b>.</p>",
    )
    assert tpl_c["success"] is True
    tid = tpl_c["template"]["template_id"]
    assert tid == "job_application"

    tpl_p = await gmail_preview_template(
        gmail_client,
        template_id=tid,
        variables={"name": "Alex <script>", "position": "AI Engineer"},
    )
    assert tpl_p["success"] is True
    assert "&lt;script&gt;" in tpl_p["rendered_body_html"]

    tpl_u = await gmail_update_template(
        gmail_client,
        template_id=tid,
        subject="Updated Application for {{position}}",
    )
    assert tpl_u["success"] is True
    assert tpl_u["template"]["version"] == 2

    # Can still retrieve version 1
    tpl_v1 = await gmail_get_template(gmail_client, template_id=tid, version=1)
    assert tpl_v1["success"] is True
    assert tpl_v1["template"]["subject"] == "Application for {{position}}"

    tpl_list = await gmail_list_templates(gmail_client)
    assert tpl_list["count"] == 1

    tpl_del = await gmail_delete_template(gmail_client, template_id=tid)
    assert tpl_del["success"] is True

    # Labels
    lbl_c = await gmail_create_label(gmail_client, label_name="Job Applications")
    assert lbl_c["success"] is True
    lid = lbl_c["label_id"]

    mock_backend.seed_message(msg_id="m_lbl_1", label_ids=["INBOX", "UNREAD"])
    mock_backend.seed_message(msg_id="m_lbl_2", label_ids=["INBOX", "UNREAD"])

    app_res = await gmail_apply_label(
        gmail_client,
        message_ids=["m_lbl_1", "m_lbl_2"],
        label_name="Job Applications",
    )
    assert app_res["success"] is True
    assert lid in mock_backend.messages["m_lbl_1"]["labelIds"]

    rem_res = await gmail_remove_label(
        gmail_client,
        message_ids=["m_lbl_1"],
        label_ids=[lid],
    )
    assert rem_res["success"] is True
    assert lid not in mock_backend.messages["m_lbl_1"]["labelIds"]

    lbl_u = await gmail_update_label(gmail_client, label_id=lid, new_name="Job Applications/2026")
    assert lbl_u["success"] is True

    lbl_all = await gmail_list_labels(gmail_client)
    assert lbl_all["success"] is True

    lbl_d = await gmail_delete_label(gmail_client, label_id=lid)
    assert lbl_d["success"] is True

    # Mailbox modification: mark_read, archive, star, move_to_trash, restore_from_trash
    await gmail_modify_messages(gmail_client, action="mark_read", message_ids=["m_lbl_1"])
    assert "UNREAD" not in mock_backend.messages["m_lbl_1"]["labelIds"]

    await gmail_modify_messages(gmail_client, action="archive", message_ids=["m_lbl_1", "m_lbl_2"])
    assert "INBOX" not in mock_backend.messages["m_lbl_1"]["labelIds"]

    await gmail_modify_messages(gmail_client, action="star", message_ids=["m_lbl_1"])
    assert "STARRED" in mock_backend.messages["m_lbl_1"]["labelIds"]

    await gmail_modify_messages(gmail_client, action="move_to_trash", message_ids=["m_lbl_1"])
    assert "TRASH" in mock_backend.messages["m_lbl_1"]["labelIds"]

    await gmail_modify_messages(gmail_client, action="restore_from_trash", message_ids=["m_lbl_1"])
    assert "TRASH" not in mock_backend.messages["m_lbl_1"]["labelIds"]

    # Filters
    flt_c = await gmail_create_filter(
        gmail_client,
        criteria={"from": "recruiter@example.com", "hasAttachment": True},
        actions={"addLabelIds": ["STARRED"], "mark_read": True},
    )
    assert flt_c["success"] is True
    fid = flt_c["filter_id"]

    flt_g = await gmail_get_filter(gmail_client, filter_id=fid)
    assert flt_g["success"] is True

    flt_l = await gmail_list_filters(gmail_client)
    assert flt_l["count"] == 1

    flt_d = await gmail_delete_filter(gmail_client, filter_id=fid)
    assert flt_d["success"] is True

    # Settings
    settings_res = await gmail_get_mailbox_settings(gmail_client)
    assert settings_res["success"] is True
    assert len(settings_res["send_as_aliases"]) >= 1

    vac_res = await gmail_update_vacation_settings(
        gmail_client,
        enable_auto_reply=True,
        response_subject="Out of Office",
        response_body_plain_text="Back Monday.",
        confirm_update=True,
    )
    assert vac_res["success"] is True
    assert vac_res["vacation_settings"]["enableAutoReply"] is True


@pytest.mark.asyncio()
async def test_scheduling_persistence_across_restart_and_failure_handling(
    gmail_client: GmailClientWrapper,
    mock_backend: MockGmailBackend,
    temp_workspace: dict[str, Path],
) -> None:
    """Verify scheduled sends survive database restart, avoid duplicate sends, and record failures cleanly."""
    # Use a file-backed SQLite database to simulate server restart
    db_file = temp_workspace["root"] / "persistent_sched.db"
    gmail_client.db = DatabaseManager(db_file)
    from gmail_mcp.storage.repositories import ScheduledJobRepository, SendLedgerRepository
    gmail_client.scheduled_job_repo = ScheduledJobRepository(gmail_client.db)
    gmail_client.send_ledger_repo = SendLedgerRepository(gmail_client.db)

    sched = await gmail_schedule_send(
        gmail_client,
        recipient="future@example.com",
        scheduled_at="2026-10-01T10:00:00+05:30",  # already due
        subject="Scheduled Reminder",
        body_text="Don't forget our meeting.",
    )
    assert sched["success"] is True
    jid = sched["job_id"]

    # Simulate restart by re-instantiating DatabaseManager & repository on the same SQLite file
    restarted_db = DatabaseManager(db_file)
    gmail_client.db = restarted_db
    gmail_client.scheduled_job_repo = ScheduledJobRepository(restarted_db)
    gmail_client.send_ledger_repo = SendLedgerRepository(restarted_db)

    listed = await gmail_list_scheduled_sends(gmail_client, status="PENDING")
    assert listed["count"] == 1

    processed = await gmail_process_due_scheduled_sends(gmail_client, current_epoch=time.time() + 10)
    assert processed["success"] is True
    assert processed["sent_count"] == 1

    # Processing again must not resend
    processed_again = await gmail_process_due_scheduled_sends(gmail_client, current_epoch=time.time() + 20)
    assert processed_again["sent_count"] == 0

    # Schedule a second job and inject an expired-token / 401 failure during execution
    sched_fail = await gmail_schedule_send(
        gmail_client,
        recipient="failtarget@example.com",
        scheduled_at="2026-10-01T11:00:00+05:30",
        subject="Will Fail",
        body_text="Testing failure recording",
    )
    fail_jid = sched_fail["job_id"]
    mock_backend.inject_fault("messages.send", make_http_error(401, "authError", "Token expired or revoked"))
    mock_backend.inject_fault("messages.send", make_http_error(401, "authError", "Token expired or revoked"))

    proc_fail = await gmail_process_due_scheduled_sends(gmail_client, current_epoch=time.time() + 30)
    assert proc_fail["success"] is False
    assert proc_fail["failed_count"] == 1
    job_rec = gmail_client.scheduled_job_repo.get(fail_jid)
    assert job_rec is not None and job_rec.status == "FAILED"
    assert job_rec.last_result is not None and job_rec.last_result["error_code"] == "AUTH_REAUTHORIZATION_REQUIRED"

    # Schedule a third job and cancel it
    sched_cancel = await gmail_schedule_send(
        gmail_client,
        recipient="cancel@example.com",
        scheduled_at="2027-12-01T10:00:00+00:00",
        subject="Cancel Me",
        body_text="Body",
    )
    can_res = await gmail_cancel_scheduled_send(gmail_client, job_id=sched_cancel["job_id"])
    assert can_res["success"] is True


@pytest.mark.asyncio()
async def test_incremental_synchronization_deduplication_and_expired_history_fallback(
    gmail_client: GmailClientWrapper,
    mock_backend: MockGmailBackend,
) -> None:
    """Test watch, incremental sync, duplicate Pub/Sub notification suppression, and 404 expired historyId resync."""
    watch_res = await gmail_start_watch(
        gmail_client,
        topic_name="projects/my-proj/topics/gmail-events",
    )
    assert watch_res["success"] is True

    mock_backend.history_records.append(
        {
            "id": "1005",
            "messagesAdded": [{"message": {"id": "m_new_1", "threadId": "th_new_1"}}],
        }
    )
    mock_backend.history_id = 1005

    sync1 = await gmail_sync_history(gmail_client, notification_id="notif_abc_1")
    assert sync1["success"] is True
    assert sync1["latest_history_id"] == "1005"
    assert sync1["summary"]["messages_added"] == 1

    # Repeated notification with same notification_id must be deduplicated
    sync_dup = await gmail_sync_history(gmail_client, notification_id="notif_abc_1")
    assert sync_dup["success"] is True
    assert sync_dup["deduplicated_notification"] is True

    # Expired history ID (404) triggers automatic full resynchronization
    mock_backend.expired_history_ids.add("1005")
    mock_backend.seed_message(msg_id="m_resync_1", subject="Resynced Email")
    mock_backend.history_id = 2000

    sync_expired = await gmail_sync_history(gmail_client, notification_id="notif_abc_2")
    assert sync_expired["success"] is True
    assert sync_expired["full_resync_triggered"] is True
    assert sync_expired["latest_history_id"] == "2000"

    stop_res = await gmail_stop_watch(gmail_client)
    assert stop_res["success"] is True


@pytest.mark.asyncio()
async def test_export_messages_json_csv_eml_and_attachments(
    gmail_client: GmailClientWrapper,
    mock_backend: MockGmailBackend,
    temp_workspace: dict[str, Path],
) -> None:
    """Verify exporting matching messages to JSON, CSV, and EML with attachment preservation."""
    mock_backend.seed_message(
        msg_id="m_exp_1",
        sender="boss@company.com",
        subject="Q4 Report",
        body_text="Report body",
        attachments=[("report.pdf", "application/pdf", b"%PDF-1.4 report")],
    )
    exp_dir = str(temp_workspace["exports"])

    res_json = await gmail_export_messages(
        gmail_client,
        query="from:boss@company.com",
        export_format="json",
        destination_directory=exp_dir,
        filename_prefix="q4_json",
        include_attachments=True,
    )
    assert res_json["success"] is True
    assert len(res_json["exported_files"]) == 1
    assert len(res_json["saved_attachments"]) == 1

    res_csv = await gmail_export_messages(
        gmail_client,
        query="from:boss@company.com",
        export_format="csv",
        destination_directory=exp_dir,
        filename_prefix="q4_csv",
    )
    assert res_csv["success"] is True
    assert Path(res_csv["exported_files"][0]).exists()

    res_eml = await gmail_export_messages(
        gmail_client,
        query="from:boss@company.com",
        export_format="eml",
        destination_directory=exp_dir,
        filename_prefix="q4_eml",
    )
    assert res_eml["success"] is True
    assert Path(res_eml["exported_files"][0]).suffix == ".eml"


@pytest.mark.asyncio()
async def test_bulk_create_drafts_parallel_processing_and_any_attachment_path(
    gmail_client: GmailClientWrapper,
    temp_workspace: dict[str, Path],
) -> None:
    """Verify high-throughput parallel draft generation with arbitrary attachment paths."""
    from gmail_mcp.tools.bulk_operations import gmail_bulk_create_drafts

    # Create an arbitrary external cover letter outside workspace attachments folder
    external_dir = temp_workspace["root"] / "external_custom_docs"
    external_dir.mkdir(parents=True, exist_ok=True)
    cover_letter = external_dir / "cover_letter.pdf"
    cover_letter.write_text("Dummy cover letter text", encoding="utf-8")

    recipients = [
        {"to": f"candidate_{i}@example.com", "name": f"Candidate {i}", "company": f"Corp {i}"}
        for i in range(15)
    ]

    res = await gmail_bulk_create_drafts(
        gmail_client,
        recipients=recipients,
        subject="Application for {company}",
        body_text="Hello {name}, please find my cover letter attached.",
        attachments=[str(cover_letter)],
        concurrency=5,
        compact=False,
    )
    assert res["success"] is True, res
    assert res["created"] == 15
    assert res["concurrency_used"] == 5
    assert len(res["draft_ids"]) == 15
    assert res["drafts"][0]["attachment_count"] == 1


@pytest.mark.asyncio()
async def test_bulk_create_drafts_threaded_replies(
    gmail_client: GmailClientWrapper,
) -> None:
    """Verify gmail_bulk_create_drafts attaches threadId and RFC In-Reply-To/References headers."""
    import base64
    from email import message_from_bytes
    from gmail_mcp.tools.bulk_operations import gmail_bulk_create_drafts

    created_payloads: list[dict] = []
    orig_execute = gmail_client.execute_api

    async def _spy_execute(endpoint, params=None, **kwargs):
        if endpoint == "drafts.create" and params:
            created_payloads.append(params["body"])
        return await orig_execute(endpoint, params, **kwargs)

    gmail_client.execute_api = _spy_execute  # type: ignore[method-assign]

    recipients = [
        {
            "to": f"recruiter_{i}@example.com",
            "name": f"Recruiter {i}",
            "company": f"Acme {i}",
            "thread_id": f"th_{i}",
            "in_reply_to": f"<orig-{i}@mail.gmail.com>",
            "references": f"<orig-{i}@mail.gmail.com>",
            "subject": f"Re: Role at Acme {i}",
        }
        for i in range(3)
    ]

    res = await gmail_bulk_create_drafts(
        gmail_client,
        recipients=recipients,
        body_text="Hi {name}, following up at {company}.",
        concurrency=3,
        compact=False,
    )
    assert res["success"] is True, res
    assert res["created"] == 3
    assert len(created_payloads) == 3

    for i, payload in enumerate(created_payloads):
        msg_obj = payload["message"]
        assert msg_obj["threadId"] == f"th_{i}"
        raw_bytes = base64.urlsafe_b64decode(msg_obj["raw"] + "==")
        parsed = message_from_bytes(raw_bytes)
        assert parsed["In-Reply-To"] == f"<orig-{i}@mail.gmail.com>"
        assert parsed["References"] == f"<orig-{i}@mail.gmail.com>"
        assert parsed["Subject"] == f"Re: Role at Acme {i}"


@pytest.mark.asyncio()
async def test_bulk_create_drafts_auto_extracts_from_in_reply_to(
    gmail_client: GmailClientWrapper,
) -> None:
    """Verify passing only in_reply_to auto-populates to, thread_id, subject, recipient_name, and company."""
    import base64
    from email import message_from_bytes
    from gmail_mcp.tools.bulk_operations import gmail_bulk_create_drafts

    created_payloads: list[dict] = []
    orig_execute = gmail_client.execute_api

    async def _spy_execute(endpoint, params=None, **kwargs):
        if endpoint == "messages.list":
            return {"messages": [{"id": "msg_parent_1"}]}, {"quota_estimate": 5}
        if endpoint == "messages.get":
            raw_parent = base64.urlsafe_b64encode(
                b"From: Jay Singh <jays.iitkgp@gmail.com>\r\n"
                b"To: susmitlakra@fpl.tech\r\n"
                b"Subject: Application for DS/AI/ML Full Time Opportunities at OneCard\r\n"
                b"Message-ID: <orig-onecard@mail.gmail.com>\r\n\r\n"
                b"Hello Susmit,\r\nMy name is Jay Singh."
            ).decode("ascii").rstrip("=")
            return {
                "id": "msg_parent_1",
                "threadId": "th_onecard_1",
                "labelIds": ["SENT"],
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Jay Singh <jays.iitkgp@gmail.com>"},
                        {"name": "To", "value": "susmitlakra@fpl.tech"},
                        {"name": "Subject", "value": "Application for DS/AI/ML Full Time Opportunities at OneCard"},
                        {"name": "Message-ID", "value": "<orig-onecard@mail.gmail.com>"},
                    ],
                    "mimeType": "text/plain",
                    "body": {"data": base64.urlsafe_b64encode(b"Hello Susmit,\nMy name is Jay Singh.").decode("ascii")},
                },
                "raw": raw_parent,
            }, {"quota_estimate": 20}
        if endpoint == "drafts.create" and params:
            created_payloads.append(params["body"])
        return await orig_execute(endpoint, params, **kwargs)

    gmail_client.execute_api = _spy_execute  # type: ignore[method-assign]

    from gmail_mcp.tools.templates import gmail_create_template

    await gmail_create_template(
        gmail_client,
        template_id="auto_reply_tpl",
        name="Auto Reply Test",
        subject="Following up: {{company}}",
        body_text="Hi {{recipient_name}}, following up at {{company}}.",
        variables=["recipient_name", "company"],
    )

    res = await gmail_bulk_create_drafts(
        gmail_client,
        recipients=[{"in_reply_to": "<orig-onecard@mail.gmail.com>"}],
        template_id="auto_reply_tpl",
        compact=False,
    )
    assert res["success"] is True, res
    assert res["created"] == 1
    msg_obj = created_payloads[0]["message"]
    assert msg_obj["threadId"] == "th_onecard_1"
    raw_bytes = base64.urlsafe_b64decode(msg_obj["raw"] + "==")
    parsed = message_from_bytes(raw_bytes)
    assert parsed["To"] == "susmitlakra@fpl.tech"
    assert parsed["Subject"] == "Re: Application for DS/AI/ML Full Time Opportunities at OneCard"
    assert parsed["In-Reply-To"] == "<orig-onecard@mail.gmail.com>"
    assert "Hi Susmit, following up at OneCard." in parsed.get_payload(decode=True).decode("utf-8")


@pytest.mark.asyncio()
async def test_bulk_create_drafts_metadata_first_query_and_run_async(
    gmail_client: GmailClientWrapper,
) -> None:
    """Verify query-based batching (zero per-item lookups) and run_async=True job tracking."""
    import asyncio
    from gmail_mcp.tools.bulk_operations import gmail_bulk_create_drafts
    from gmail_mcp.tools.sending import gmail_get_bulk_status
    from gmail_mcp.tools.templates import gmail_create_template

    await gmail_create_template(
        gmail_client,
        template_id="fast_bulk_tpl",
        name="Fast Bulk Test",
        subject="Following up: {{company}}",
        body_text="Hi {{recipient_name}}, following up at {{company}}.",
        variables=["recipient_name", "company"],
    )

    get_calls: list[dict] = []
    orig_execute = gmail_client.execute_api

    async def _spy_execute(endpoint, params=None, **kwargs):
        if endpoint == "messages.list":
            return {"messages": [{"id": "m1"}, {"id": "m2"}]}, {"quota_estimate": 5}
        if endpoint == "messages.get":
            get_calls.append(params or {})
            idx = 1 if (params or {}).get("id") == "m1" else 2
            name = "Alice" if idx == 1 else "Bob"
            return {
                "id": f"m{idx}",
                "threadId": f"th_{idx}",
                "labelIds": ["SENT"],
                "snippet": f"Hello {name}, My name is Jay Singh.",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Jay Singh <jays.iitkgp@gmail.com>"},
                        {"name": "To", "value": f"{name.lower()}@corp{idx}.com"},
                        {"name": "Subject", "value": f"Application at Corp{idx}"},
                        {"name": "Message-ID", "value": f"<orig-{idx}@mail.gmail.com>"},
                    ],
                },
            }, {"quota_estimate": 20}
        return await orig_execute(endpoint, params, **kwargs)

    gmail_client.execute_api = _spy_execute  # type: ignore[method-assign]

    res = await gmail_bulk_create_drafts(
        gmail_client,
        query="in:sent after:2026/11/19",
        max_results=10,
        template_id="fast_bulk_tpl",
        run_async=True,
    )
    assert res["success"] is True, res
    assert res["status"] == "RUNNING"
    assert res["total"] == 2
    assert res["job_id"].startswith("job_create_drafts_")
    # Only metadata fetches from gmail_search_messages; zero format='full' downloads
    assert all(c.get("format") == "metadata" for c in get_calls)

    await asyncio.sleep(0.05)
    status = await gmail_get_bulk_status(gmail_client, res["job_id"])
    assert status["success"] is True
    assert status["status"] == "COMPLETED"
    assert status["sent"] == 2
    assert status["failed"] == 0


@pytest.mark.asyncio()
async def test_bulk_delete_drafts_and_alias(
    gmail_client: GmailClientWrapper,
) -> None:
    """Verify bulk draft deletion: ID list, query discovery, reply filtering, dry-run, async mode, and alias."""
    import asyncio
    from gmail_mcp.tools.bulk_operations import (
        gmail_bulk_delete_draft,
        gmail_bulk_delete_drafts,
    )
    from gmail_mcp.tools.sending import gmail_get_bulk_status

    deleted_calls: list[str] = []

    async def _mock_execute(endpoint, params=None, **kwargs):
        if endpoint == "drafts.list":
            return {
                "drafts": [
                    {"id": "d_reply_1"},
                    {"id": "d_reply_2"},
                    {"id": "d_normal_3"},
                ]
            }, {"quota_estimate": 5}
        if endpoint == "drafts.get":
            did = (params or {}).get("id")
            if did in ("d_reply_1", "d_reply_2"):
                headers = [
                    {"name": "Subject", "value": "Re: Opportunity"},
                    {"name": "In-Reply-To", "value": "<ref@corp.com>"},
                ]
            else:
                headers = [{"name": "Subject", "value": "New Outreach"}]
            return {
                "id": did,
                "message": {"payload": {"headers": headers}},
            }, {"quota_estimate": 5}
        if endpoint == "drafts.delete":
            deleted_calls.append((params or {}).get("id"))
            return {}, {"quota_estimate": 10}
        return {}, {}

    gmail_client.execute_api = _mock_execute  # type: ignore[method-assign]

    # 1. Dry run with query + filter_reply_only
    dry_res = await gmail_bulk_delete_drafts(
        gmail_client,
        query="in:drafts",
        filter_reply_only=True,
        dry_run=True,
    )
    assert dry_res["success"] is True
    assert dry_res["dry_run"] is True
    assert dry_res["target_count"] == 2
    assert dry_res["draft_ids"] == ["d_reply_1", "d_reply_2"]
    assert len(deleted_calls) == 0

    # 2. Synchronous execution with filter_reply_only
    del_res = await gmail_bulk_delete_drafts(
        gmail_client,
        filter_reply_only=True,
        dry_run=False,
    )
    assert del_res["success"] is True
    assert del_res["deleted_count"] == 2
    assert del_res["deleted_draft_ids"] == ["d_reply_1", "d_reply_2"]
    assert deleted_calls == ["d_reply_1", "d_reply_2"]

    # 3. Explicit list with alias gmail_bulk_delete_draft
    alias_res = await gmail_bulk_delete_draft(
        gmail_client,
        draft_ids=["d_custom_1", "d_custom_2"],
    )
    assert alias_res["success"] is True
    assert alias_res["deleted_count"] == 2
    assert "d_custom_1" in deleted_calls
    assert "d_custom_2" in deleted_calls

    # 4. Async execution
    async_res = await gmail_bulk_delete_drafts(
        gmail_client,
        draft_ids=["d_async_1"],
        run_async=True,
    )
    assert async_res["success"] is True
    assert async_res["status"] == "RUNNING"
    assert async_res["job_id"].startswith("job_delete_drafts_")

    await asyncio.sleep(0.05)
    job_status = await gmail_get_bulk_status(gmail_client, async_res["job_id"])
    assert job_status["success"] is True
    assert job_status["status"] == "COMPLETED"
    assert job_status["sent"] == 1
    assert "d_async_1" in deleted_calls


@pytest.mark.asyncio()
async def test_bulk_create_drafts_skips_bounced_threads(
    gmail_client: GmailClientWrapper,
) -> None:
    """Verify gmail_bulk_create_drafts inspects reply threads and skips bounced threads by default."""
    from gmail_mcp.tools.bulk_operations import gmail_bulk_create_drafts

    created_threads: list[str] = []
    orig_execute = gmail_client.execute_api

    fake_threads = {
        "th_clean": {
            "id": "th_clean",
            "messages": [
                {
                    "id": "m_clean",
                    "threadId": "th_clean",
                    "snippet": "Hello Alice, applying for ML role.",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "Jay <jay@example.com>"},
                            {"name": "To", "value": "alice@acme.com"},
                            {"name": "Subject", "value": "Application at Acme"},
                        ]
                    },
                }
            ],
        },
        "th_mailer_daemon": {
            "id": "th_mailer_daemon",
            "messages": [
                {"id": "m_b1_orig", "threadId": "th_mailer_daemon", "snippet": "Hello Bob", "payload": {"headers": []}},
                {
                    "id": "m_b1_dsn",
                    "threadId": "th_mailer_daemon",
                    "snippet": "Message blocked Your message to bob@ltimindtree.com has been blocked.",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "Mail Delivery Subsystem <mailer-daemon@googlemail.com>"},
                            {"name": "Subject", "value": "Delivery Status Notification (Failure)"},
                        ]
                    },
                },
            ],
        },
        "th_outlook_550": {
            "id": "th_outlook_550",
            "messages": [
                {"id": "m_b2_orig", "threadId": "th_outlook_550", "snippet": "Hello Carol", "payload": {"headers": []}},
                {
                    "id": "m_b2_dsn",
                    "threadId": "th_outlook_550",
                    "snippet": "Remote server returned '550 5.1.1 User unknown' from protection.outlook.com",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "postmaster@corp.com"},
                            {"name": "Subject", "value": "Undeliverable: Application at Corp"},
                        ]
                    },
                },
            ],
        },
        "th_delay": {
            "id": "th_delay",
            "messages": [
                {
                    "id": "m_b3_dsn",
                    "threadId": "th_delay",
                    "snippet": "Delivery incomplete There was a temporary problem delivering your message.",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "postmaster@other.com"},
                            {"name": "Subject", "value": "Delivery Status Notification (Delay)"},
                        ]
                    },
                },
            ],
        },
    }

    async def _mock_execute(endpoint, params=None, **kwargs):
        if endpoint == "threads.get" and params and params.get("id") in fake_threads:
            return fake_threads[params["id"]], {"quota_estimate": 40}
        if endpoint == "messages.get" and params and params.get("id") == "m_b1_dsn":
            return fake_threads["th_mailer_daemon"]["messages"][1], {"quota_estimate": 20}
        if endpoint == "drafts.create" and params:
            created_threads.append(params["body"]["message"].get("threadId", ""))
        return await orig_execute(endpoint, params, **kwargs)

    gmail_client.execute_api = _mock_execute  # type: ignore[method-assign]

    recipients = [
        {"to": "alice@acme.com", "thread_id": "th_clean", "subject": "Re: Application at Acme"},
        {"to": "bob@ltimindtree.com", "thread_id": "th_mailer_daemon", "subject": "Re: Application at LTIMindtree"},
        {"to": "carol@corp.com", "thread_id": "th_outlook_550", "subject": "Re: Application at Corp"},
        {"to": "dave@other.com", "thread_id": "th_delay", "subject": "Re: Application at Other"},
    ]

    res = await gmail_bulk_create_drafts(
        gmail_client,
        recipients=recipients,
        body_text="Following up!",
        compact=False,
        concurrency=2,
    )
    assert res["success"] is True, res
    assert res["created"] == 1
    assert res["skipped"] == 3
    assert res["failed"] == 0
    assert created_threads == ["th_clean"]
    assert len(res["skipped_items"]) == 3
    assert all(item["reason"] == "THREAD_BOUNCED" for item in res["skipped_items"])

    # Verify skip_bounced=False bypasses the bounce check
    created_threads.clear()
    res_no_skip = await gmail_bulk_create_drafts(
        gmail_client,
        recipients=recipients[:2],
        body_text="Following up!",
        compact=True,
        skip_bounced=False,
    )
    assert res_no_skip["success"] is True
    assert res_no_skip["created"] == 2
    assert res_no_skip["skipped"] == 0





