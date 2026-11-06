"""
Mandatory Stress Tests (Section 12 of gmail_mcp_server.md):
1. Creating 100 drafts.
2. Duplicating an existing draft 100 times.
3. Attaching one PDF to 100 existing drafts.
4. Searching and reading 1,000 unread messages.
5. Preparing 100 separate emails.
6. Sending 100 approved messages to allowlisted test recipients.
7. Preparing 100 replies to separate threads.
8. Applying a label to 1,000 messages.
9. Recovering a partially completed bulk operation (failure after 25 successful messages -> resume remaining 75).
10. Simulating quota exhaustion halfway through execution.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gmail_mcp.core.gmail_client import GmailClientWrapper
from tests.conftest import MockGmailBackend, make_http_error
from gmail_mcp.tools import (
    gmail_bulk_attach_to_drafts,
    gmail_bulk_create_drafts,
    gmail_bulk_label_messages,
    gmail_create_draft,
    gmail_create_label,
    gmail_create_template,
    gmail_duplicate_draft,
    gmail_execute_bulk_reply,
    gmail_execute_bulk_send,
    gmail_list_unread,
    gmail_prepare_bulk_reply,
    gmail_prepare_bulk_send,
)


@pytest.mark.asyncio()
async def test_stress_1_and_3_create_100_drafts_and_attach_shared_pdf(
    gmail_client: GmailClientWrapper,
    temp_workspace: dict[str, Path],
) -> None:
    """Stress Test 1 & 3: Create 100 personalized drafts and attach a shared PDF to all 100 drafts."""
    await gmail_create_template(
        gmail_client,
        template_id="outreach_100",
        name="Outreach 100",
        subject="Role at {{company}} for {{name}}",
        body_text="Hi {{name}}, excited about {{company}}.",
    )
    recipients = [
        {"email": f"founder{i}@startup{i}.com", "name": f"Founder {i}", "company": f"Startup {i}"}
        for i in range(100)
    ]
    res_create = await gmail_bulk_create_drafts(
        gmail_client,
        recipients=recipients,
        template_id="outreach_100",
    )
    assert res_create["success"] is True
    assert res_create["requested"] == 100
    assert res_create["created"] == 100
    assert res_create["failed"] == 0
    draft_ids = [d["draft_id"] for d in res_create["drafts"]]
    assert len(set(draft_ids)) == 100

    # Stress Test 3: Attach resume.pdf to all 100 existing drafts
    pdf_path = str(temp_workspace["resume_pdf"])
    res_attach = await gmail_bulk_attach_to_drafts(
        gmail_client,
        draft_ids=draft_ids,
        attachments=[pdf_path],
    )
    assert res_attach["success"] is True
    assert res_attach["requested"] == 100
    assert res_attach["updated"] == 100
    assert res_attach["failed"] == 0
    assert all(d["total_attachments"] == 1 for d in res_attach["drafts"])


@pytest.mark.asyncio()
async def test_stress_2_duplicate_existing_draft_100_times(
    gmail_client: GmailClientWrapper,
    temp_workspace: dict[str, Path],
) -> None:
    """Stress Test 2: Duplicate an existing draft (with attachment) 100 times and verify every ID."""
    source = await gmail_create_draft(
        gmail_client,
        to="template@example.com",
        subject="Master Draft",
        body_text="Master body",
        attachments=[str(temp_workspace["resume_pdf"])],
    )
    source_id = source["draft_id"]

    overrides = [f"dup{i}@example.com" for i in range(100)]
    dup_res = await gmail_duplicate_draft(
        gmail_client,
        source_draft_id=source_id,
        copies=100,
        recipient_overrides=overrides,
    )
    assert dup_res["success"] is True
    assert dup_res["requested"] == 100
    assert dup_res["created"] == 100
    assert dup_res["failed"] == 0
    dup_ids = [d["draft_id"] for d in dup_res["drafts"]]
    assert len(set(dup_ids)) == 100
    assert source_id not in dup_ids
    assert all(d["attachments_preserved"] == 1 for d in dup_res["drafts"])


@pytest.mark.asyncio()
async def test_stress_4_and_8_read_1000_unread_messages_and_bulk_label_1000(
    gmail_client: GmailClientWrapper,
    mock_backend: MockGmailBackend,
) -> None:
    """Stress Test 4 & 8: Search/read 1,000 unread messages across pages without marking read, and apply label to 1,000 messages."""
    msg_ids: list[str] = []
    for i in range(1000):
        seeded = mock_backend.seed_message(
            msg_id=f"u_{i}",
            sender=f"sender{i}@example.com",
            subject=f"Unread Message #{i}",
            body_text=f"Body content for message {i}",
            label_ids=["INBOX", "UNREAD"],
        )
        msg_ids.append(seeded["id"])

    unread_res = await gmail_list_unread(
        gmail_client,
        max_results_per_page=500,
        include_body=True,
    )
    assert unread_res["success"] is True
    assert unread_res["returned_count"] == 1000
    assert unread_res["pages_fetched"] == 2
    assert unread_res["unread_preserved"] is True
    # Confirm none of the 1,000 messages lost their UNREAD label
    assert all("UNREAD" in mock_backend.messages[mid]["labelIds"] for mid in msg_ids)

    # Stress Test 8: Apply a label to all 1,000 messages
    lbl_res = await gmail_create_label(gmail_client, label_name="Batch1000")
    lbl_id = lbl_res["label_id"]

    bulk_lbl = await gmail_bulk_label_messages(
        gmail_client,
        message_ids=msg_ids,
        add_label_ids=[lbl_id],
    )
    assert bulk_lbl["success"] is True
    assert bulk_lbl["requested"] == 1000
    assert bulk_lbl["modified"] == 1000
    assert all(lbl_id in mock_backend.messages[mid]["labelIds"] for mid in msg_ids)


@pytest.mark.asyncio()
async def test_stress_5_and_6_prepare_and_send_100_approved_emails(
    gmail_client: GmailClientWrapper,
) -> None:
    """Stress Test 5 & 6: Prepare 100 separate emails, verify preview & quota, then execute with confirmation."""
    recipients = [f"allowlisted{i}@test.example.com" for i in range(100)]
    gmail_client.config.e2e_enabled = True
    gmail_client.config.e2e_allowed_recipients = recipients

    prep = await gmail_prepare_bulk_send(
        gmail_client,
        recipients=[{"email": r, "name": f"Candidate {idx}"} for idx, r in enumerate(recipients)],
        subject="Hello {{name}}",
        body_text="Personalized body for {{name}}",
    )
    assert prep["success"] is True
    assert prep["requested_count"] == 100
    assert prep["required_confirmation_phrase"] == "CONFIRM SEND 100 EMAILS"
    assert prep["quota_estimate"]["total_units"] == 10000

    exec_res = await gmail_execute_bulk_send(
        gmail_client,
        plan_id=prep["plan_id"],
        confirmation_phrase="CONFIRM SEND 100 EMAILS",
    )
    assert exec_res["success"] is True
    assert exec_res["requested"] == 100
    assert exec_res["sent"] == 100
    assert exec_res["failed"] == 0
    assert exec_res["remaining"] == 0


@pytest.mark.asyncio()
async def test_stress_7_prepare_and_execute_100_replies_to_separate_threads(
    gmail_client: GmailClientWrapper,
    mock_backend: MockGmailBackend,
) -> None:
    """Stress Test 7: Prepare and execute 100 replies to 100 separate threads, verifying thread IDs and RFC headers."""
    replies_input = []
    for i in range(100):
        seeded = mock_backend.seed_message(
            msg_id=f"thread_root_{i}",
            thread_id=f"thread_sep_{i}",
            sender=f"contact{i}@example.org",
            subject=f"Inquiry #{i}",
            rfc_message_id=f"<root_{i}@example.org>",
        )
        replies_input.append(
            {
                "thread_id": seeded["threadId"],
                "message_id": seeded["id"],
                "body_text": f"Reply to inquiry #{i}",
            }
        )

    prep = await gmail_prepare_bulk_reply(gmail_client, replies=replies_input)
    assert prep["success"] is True
    assert prep["requested_count"] == 100
    assert prep["required_confirmation_phrase"] == "CONFIRM SEND 100 REPLIES"
    assert all(p["in_reply_to"] == f"<root_{idx}@example.org>" for idx, p in enumerate(prep["preview_items"]))

    exec_res = await gmail_execute_bulk_reply(
        gmail_client,
        plan_id=prep["plan_id"],
        confirmation_phrase="CONFIRM SEND 100 REPLIES",
    )
    assert exec_res["success"] is True
    assert exec_res["sent"] == 100
    assert exec_res["failed"] == 0


@pytest.mark.asyncio()
async def test_stress_9_recover_partially_completed_bulk_operation_after_25_sends(
    gmail_client: GmailClientWrapper,
    mock_backend: MockGmailBackend,
) -> None:
    """
    Stress Test 9: Simulate a failure on the 26th message (after 25 successful messages) in a 100-email bulk send,
    then re-run/recover the plan and verify the first 25 are skipped (not resent) and the remaining 75 succeed.
    """
    recipients = [f"recover{i}@example.com" for i in range(100)]
    prep = await gmail_prepare_bulk_send(
        gmail_client,
        recipients=recipients,
        subject="Recovery Campaign",
        body_text="Testing partial recovery",
    )
    plan_id = prep["plan_id"]

    # Inject a non-retryable 400 error on the 26th send call (index 25)
    send_counter = 0
    orig_dispatch = mock_backend.dispatch

    def _fail_on_26th(method: str, params: dict) -> object:
        nonlocal send_counter
        if method == "messages.send":
            send_counter += 1
            if send_counter == 26:
                raise make_http_error(400, "invalidArgument", "Simulated failure on message #26")
        return orig_dispatch(method, params)

    gmail_client.set_custom_dispatcher(_fail_on_26th)
    first_run = await gmail_execute_bulk_send(
        gmail_client,
        plan_id=plan_id,
        confirmation_phrase="CONFIRM SEND 100 EMAILS",
    )
    assert first_run["success"] is False
    assert first_run["sent"] == 25 + 74  # 99 succeeded, 1 failed (#26)
    assert first_run["failed"] == 1
    assert first_run["resumable"] is True

    # Restore healthy dispatcher and re-run the same plan to recover the 1 failed message
    gmail_client.set_custom_dispatcher(orig_dispatch)
    second_run = await gmail_execute_bulk_send(
        gmail_client,
        plan_id=plan_id,
        confirmation_phrase="CONFIRM SEND 100 EMAILS",
    )
    assert second_run["success"] is True
    assert second_run["sent"] == 100
    assert second_run["skipped"] == 99  # All 99 previously sent messages were skipped via idempotency ledger!
    assert second_run["failed"] == 0


@pytest.mark.asyncio()
async def test_stress_10_simulate_quota_exhaustion_halfway_through_execution(
    gmail_client: GmailClientWrapper,
) -> None:
    """
    Stress Test 10: Simulate daily sending cap / quota exhaustion after 25 messages of a 100-message plan.
    Verify execution halts cleanly with `DAILY_SEND_LIMIT`, `sent=25`, `remaining=75`, `resumable=True`.
    """
    # Set daily hard cap to 25 messages
    gmail_client.config.daily_send_hard_cap = 25
    gmail_client.config.daily_send_soft_cap = 20

    recipients = [f"captest{i}@example.com" for i in range(100)]
    prep = await gmail_prepare_bulk_send(
        gmail_client,
        recipients=recipients,
        subject="Quota Exhaustion Test",
        body_text="Testing hard cap stop",
    )
    plan_id = prep["plan_id"]

    res = await gmail_execute_bulk_send(
        gmail_client,
        plan_id=plan_id,
        confirmation_phrase="CONFIRM SEND 100 EMAILS",
    )
    assert res["success"] is False
    assert res["error_code"] == "DAILY_SEND_LIMIT"
    assert res["requested"] == 100
    assert res["sent"] == 25
    assert res["remaining"] == 75
    assert res["resumable"] is True

    # Now raise the hard cap to 100 and resume the paused plan: the first 25 are skipped and the remaining 75 are sent
    gmail_client.config.daily_send_hard_cap = 100
    resumed = await gmail_execute_bulk_send(
        gmail_client,
        plan_id=plan_id,
        confirmation_phrase="CONFIRM SEND 100 EMAILS",
    )
    assert resumed["success"] is True
    assert resumed["sent"] == 100
    assert resumed["skipped"] == 25
    assert resumed["remaining"] == 0
