"""Unit tests verifying all 6 architectural improvements from improvements.md."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gmail_mcp.core.gmail_client import GmailClientWrapper
from gmail_mcp.core.safety import SafetyError
from gmail_mcp.tools.bulk_operations import gmail_bulk_create_drafts
from gmail_mcp.tools.drafts import gmail_create_draft, gmail_get_draft
from gmail_mcp.tools.sending import (
    gmail_bulk_send_drafts,
    gmail_execute_bulk_send,
    gmail_get_bulk_status,
    gmail_prepare_bulk_send,
    gmail_send_draft,
)
from gmail_mcp.tools.templates import gmail_create_template
from tests.conftest import MockGmailBackend


@pytest.mark.asyncio()
async def test_requirement_1_native_draft_dispatching(
    gmail_client: GmailClientWrapper,
    mock_backend: MockGmailBackend,
) -> None:
    """Requirement 1: gmail_send_draft and gmail_bulk_send_drafts dispatch directly via users.drafts.send."""
    # 1. Create a draft
    created = await gmail_create_draft(
        gmail_client,
        to="founder@startup.com",
        subject="AI Partnership Inquiry",
        body_text="Hi Founder, exciting work at Startup.",
    )
    assert created["success"] is True
    draft_id = created["draft_id"]

    # 2. Dispatch via native gmail_send_draft
    sent = await gmail_send_draft(gmail_client, draft_id=draft_id, idempotency_key="key_draft_001")
    assert sent["success"] is True
    assert sent["status"] == "sent"
    assert sent["draft_id"] == draft_id
    assert sent["message_id"] is not None

    # Verify idempotency deduplication on second dispatch
    dedup = await gmail_send_draft(gmail_client, draft_id=draft_id, idempotency_key="key_draft_001")
    assert dedup["success"] is True
    assert dedup["deduplicated"] is True

    # 3. Test bulk draft dispatching
    d1 = await gmail_create_draft(gmail_client, to="a@example.com", subject="S1", body_text="B1")
    d2 = await gmail_create_draft(gmail_client, to="b@example.com", subject="S2", body_text="B2")
    bulk_sent = await gmail_bulk_send_drafts(
        gmail_client,
        draft_ids=[d1["draft_id"], d2["draft_id"]],
        concurrency=2,
        compact=True,
    )
    assert bulk_sent["success"] is True
    assert bulk_sent["sent"] == 2
    assert bulk_sent["failed"] == 0


@pytest.mark.asyncio()
async def test_requirement_2_compact_output_mode(
    gmail_client: GmailClientWrapper,
) -> None:
    """Requirement 2: compact=True returns concise operational metadata without multi-megabyte JSON payloads."""
    recipients = [
        {"email": f"user{i}@example.com", "name": f"User {i}", "company": f"Corp {i}"}
        for i in range(5)
    ]
    # 1. gmail_prepare_bulk_send with compact=True vs compact=False
    prep_compact = await gmail_prepare_bulk_send(
        gmail_client,
        recipients=recipients,
        subject="Opportunity at {{company}}",
        body_text="Hello {{name}}, welcome to {{company}}.",
        compact=True,
    )
    assert prep_compact["success"] is True
    assert prep_compact["compact"] is True
    assert "preview_items" in prep_compact
    assert len(prep_compact["preview_items"]) <= 2

    prep_full = await gmail_prepare_bulk_send(
        gmail_client,
        recipients=recipients,
        subject="Opportunity at {{company}}",
        body_text="Hello {{name}}, welcome to {{company}}.",
        compact=False,
    )
    assert prep_full["success"] is True
    assert prep_full["compact"] is False
    assert "preview_items" in prep_full
    assert len(prep_full["preview_items"]) == 5

    # 2. gmail_bulk_create_drafts with compact=True
    bulk_drafts = await gmail_bulk_create_drafts(
        gmail_client,
        recipients=recipients[:2],
        subject="Draft {{name}}",
        body_text="Content for {{company}}",
        compact=True,
    )
    assert bulk_drafts["success"] is True
    assert bulk_drafts["compact"] is True
    assert "draft_ids" in bulk_drafts
    assert len(bulk_drafts["draft_ids"]) == 2


@pytest.mark.asyncio()
async def test_requirement_3_universal_filesystem_pathing(
    gmail_client: GmailClientWrapper,
    tmp_path: Path,
) -> None:
    """Requirement 3: Attachments and recipient sources outside workspace do not raise PATH_OUTSIDE_ALLOWED_DIRS."""
    # Create external files outside the workspace in tmp_path
    ext_pdf = tmp_path / "resume_external.pdf"
    ext_pdf.write_bytes(b"%PDF-1.4 Mock external PDF bytes")

    ext_csv = tmp_path / "leads_external.csv"
    ext_csv.write_text("email,name,company\next1@test.com,Ext One,Acme\n", encoding="utf-8")

    # Preparing bulk send referencing external recipient_source and external attachment
    prep = await gmail_prepare_bulk_send(
        gmail_client,
        recipient_source=str(ext_csv),
        subject="External Lead Inquiry",
        body_text="Hello {{name}}",
        attachments=[str(ext_pdf)],
        compact=True,
    )
    assert prep["success"] is True
    assert prep["requested_count"] == 1


@pytest.mark.asyncio()
async def test_requirement_4_template_filter_pipeline(
    gmail_client: GmailClientWrapper,
) -> None:
    """Requirement 4: Template pipelines support first_name, last_name, title, upper, lower, and fallbacks."""
    safety = gmail_client.safety
    rendered = safety.render_template_content(
        subject="Hello {{ full_name | first_name }}",
        body_text=(
            "Hi {{ full_name | first_name }},\n"
            "Company: {{ company | title }}\n"
            "Role: {{ role | upper }}\n"
            "Fallback: {{ missing_val | default('Hiring Manager') }}"
        ),
        body_html=None,
        variables={
            "full_name": "Ajaay Kiran",
            "company": "apex cyber technologies",
            "role": "ai engineer",
        },
        defaults={},
    )
    assert rendered["subject"] == "Hello Ajaay"
    assert "Hi Ajaay," in rendered["body_text"]
    assert "Company: Apex Cyber Technologies" in rendered["body_text"]
    assert "Role: AI ENGINEER" in rendered["body_text"]
    assert "Fallback: Hiring Manager" in rendered["body_text"]


@pytest.mark.asyncio()
async def test_requirement_5_direct_scraped_json_ingestion(
    gmail_client: GmailClientWrapper,
    tmp_path: Path,
) -> None:
    """Requirement 5: Arbitrary scraped JSON files are ingested with field_mapping and attachment_pattern."""
    # Write a scraped JSON file with arbitrary keys
    scraped_data = {
        "startups": [
            {
                "Contact Name": "Elena Rostova",
                "Work Email": "elena@deepmind-clone.ai",
                "Organization": "DeepMindClone",
            },
            {
                "Contact Name": "Marcus Vance",
                "Work Email": "marcus@quantumleap.tech",
                "Organization": "QuantumLeap",
            },
        ]
    }
    json_path = tmp_path / "scraped_leads.json"
    json_path.write_text(json.dumps(scraped_data), encoding="utf-8")

    # Create dummy PDFs matching organization
    pdf1 = tmp_path / "DeepMindClone_resume.pdf"
    pdf1.write_bytes(b"%PDF-1.4 dummy deepmind resume")
    pdf2 = tmp_path / "QuantumLeap_resume.pdf"
    pdf2.write_bytes(b"%PDF-1.4 dummy quantum resume")

    attachment_template = str(tmp_path / "{{ company }}_resume.pdf")

    prep = await gmail_prepare_bulk_send(
        gmail_client,
        recipient_source=str(json_path),
        field_mapping={
            "email": "Work Email",
            "name": "Contact Name",
            "company": "Organization",
        },
        attachment_pattern=attachment_template,
        subject="Engineering Role at {{ company }}",
        body_text="Dear {{ name | first_name }}, I would love to join {{ company }}.",
        compact=False,
    )
    assert prep["success"] is True
    assert prep["requested_count"] == 2
    # Check that individual attachments were dynamically resolved
    previews = prep["preview_items"]
    assert "elena@deepmind-clone.ai" in previews[0]["recipients"]["to"]
    assert any("DeepMindClone_resume.pdf" in att for att in previews[0]["attachments"])
    assert "marcus@quantumleap.tech" in previews[1]["recipients"]["to"]
    assert any("QuantumLeap_resume.pdf" in att for att in previews[1]["attachments"])


@pytest.mark.asyncio()
async def test_requirement_6_concurrency_and_async_background_jobs(
    gmail_client: GmailClientWrapper,
) -> None:
    """Requirement 6: Bulk dispatching supports concurrency and run_async=True with gmail_get_bulk_status."""
    # 1. Create a staged plan
    prep = await gmail_prepare_bulk_send(
        gmail_client,
        recipients=[{"email": f"tester{i}@test.com"} for i in range(4)],
        subject="Test Async",
        body_text="Async bulk test",
        dry_run=False,
    )
    plan_id = prep["plan_id"]
    confirm = prep["required_confirmation_phrase"]

    # 2. Execute with run_async=True
    exec_async = await gmail_execute_bulk_send(
        gmail_client,
        plan_id=plan_id,
        confirmation_phrase=confirm,
        concurrency=3,
        run_async=True,
    )
    assert exec_async["success"] is True
    job_id = exec_async["job_id"]
    assert job_id is not None
    assert exec_async["status"] == "RUNNING"

    # 3. Check status via gmail_get_bulk_status
    status = await gmail_get_bulk_status(gmail_client, job_id=job_id)
    assert status["success"] is True
    assert status["job_id"] == job_id
    assert status["total"] == 4
