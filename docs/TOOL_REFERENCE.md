# Tool Reference (`gmail_mcp`)

Complete reference for all **57 MCP tools** exposed by the Gmail Automation MCP Server, including input schemas, quota unit costs, safety gates, and the **17 mandatory sample invocations** from Section 13 of `gmail_mcp_server.md`.

---

## 1. Complete Tool Inventory (57 Tools)

### Category A — Account, Profile & Diagnostics (4 Tools)
| Tool | Quota Units | Read-Only | Description |
| :--- | :---: | :---: | :--- |
| `gmail_get_profile` | 1 | Yes | Retrieve the authenticated user's email address, total messages, total threads, and current `historyId`. |
| `gmail_check_auth_status` | 0–1 | Yes | Check OAuth 2.0 token validity, expiry, granted scopes, and secret file hygiene. |
| `gmail_get_quota_status` | 0 | Yes | Inspect rolling 60-second project/user quota windows, daily send counters (`200` soft warning, `300` hard block, `500` ceiling), and active backoff status. |
| `gmail_health_check` | 1 | Yes | Verify SQLite connectivity, schema migration version, OAuth status, and optional live Gmail API reachability. |

### Category B — Search, Message Retrieval & Thread Inspection (9 Tools)
| Tool | Quota Units | Read-Only | Description |
| :--- | :---: | :---: | :--- |
| `gmail_search_messages` | 5 / page | Yes | Search messages using native Gmail query syntax or structured filters (`sender`, `recipient`, `subject`, `has_words`, `label_ids`, `is_unread`, `has_attachment`, `after`, `before`, `newer_than`, `older_than`). |
| `gmail_get_message` | 5 | Yes | Retrieve a single message by ID (`minimal`, `metadata`, `full`, or `raw`), with decoded body text/HTML and attachment metadata. |
| `gmail_batch_get_messages` | 5 × N | Yes | Retrieve multiple messages in bounded concurrent chunks with per-item error isolation. |
| `gmail_get_thread` | 10 | Yes | Retrieve an entire conversation thread and its ordered messages. |
| `gmail_get_thread_summary` | 10 | Yes | Produce a structured summary of a thread: participants, message count, timestamps, attachments, and latest snippet. |
| `gmail_find_unanswered_threads` | 10+ | Yes | Identify threads where the last message is from an external sender and awaits a reply. |
| `gmail_find_threads_awaiting_followup` | 10+ | Yes | Identify threads where the authenticated user sent the last message and no reply has arrived within `min_days_elapsed`. |
| `gmail_extract_action_items` | 5–10 | Yes | Extract structured action items, questions, dates, and links from a message or thread. |
| `gmail_summarize_thread` | 10 | Yes | Alias/companion to `gmail_get_thread_summary` returning structured chronological digest and participant breakdown. |

### Category C — Draft Creation, Bulk Drafts & Templates (12 Tools)
| Tool | Quota Units | Read-Only | Description |
| :--- | :---: | :---: | :--- |
| `gmail_create_draft` | 10 | No | Create a plain-text or HTML draft with optional `cc`, `bcc`, `reply_to`, and file attachments. |
| `gmail_update_draft` | 15 | No | Replace the MIME content and recipients of an existing draft (`drafts.update`). |
| `gmail_get_draft` | 5 | Yes | Retrieve a draft and its underlying message payload by `draft_id`. |
| `gmail_list_drafts` | 5 / page | Yes | List drafts with optional query filter and pagination (`page_token`, `max_results`). |
| `gmail_delete_draft` | 10 | No (Destructive) | Permanently delete a draft by `draft_id`. |
| `gmail_send_draft` | 100 | No | Send an existing draft via `drafts.send`, subject to safety gates (`confirm_send`, allowlist, daily cap). |
| `gmail_create_reply_draft` | 15 | No | Create a properly threaded reply draft (`threadId`, `In-Reply-To`, `References`, `Re:` subject normalization). |
| `gmail_create_forward_draft` | 15–20 | No | Create a forward draft preserving original headers and optionally carrying over original attachments. |
| `gmail_bulk_create_drafts` | 10 × N | No | Create multiple personalized drafts from a list of recipient items or a stored template (`dry_run` supported). |
| `gmail_bulk_create_drafts_with_attachments` | 10 × N | No | Create multiple personalized drafts where each item has shared and/or per-recipient attachment paths. |
| `gmail_preview_template` | 0 | Yes | Render a template with `variables` (`strict_variables=True` by default) without creating a draft or sending. |
| `gmail_create_draft_from_template` | 10 | No | Render a stored SQLite template and save it directly as a Gmail draft. |

### Category D — Sending, Replies, Forwards & Bulk Sending (7 Tools)
| Tool | Quota Units | Read-Only | Description |
| :--- | :---: | :---: | :--- |
| `gmail_send_email` | 100 | No | Compose and immediately send a plain-text or HTML email with optional attachments, idempotency check, and timeout reconciliation. |
| `gmail_reply_to_email` | 105 | No | Fetch the target message's RFC 2822 headers (`Message-ID`, `References`, `threadId`) and send a threaded reply (`reply_all` supported). |
| `gmail_forward_email` | 105 | No | Forward an existing message to new recipients with optional comment and preserved attachments. |
| `gmail_send_from_template` | 100 | No | Render a stored SQLite template with `variables` and send immediately. |
| `gmail_bulk_send_emails` | 100 × N | No | Send multiple personalized emails sequentially with configurable pacing delay, daily send-cap enforcement, and per-item isolation. |
| `gmail_bulk_send_with_attachments` | 100 × N | No | Send multiple personalized emails with shared and per-recipient attachments. |
| `gmail_verify_sent_message` | 5 | Yes | Reconcile an ambiguous send by searching `rfc822msgid:<Message-ID>` in `SENT` to confirm whether Gmail delivered the message. |

### Category E — Reusable Email Templates (5 Tools)
| Tool | Quota Units | Read-Only | Description |
| :--- | :---: | :---: | :--- |
| `gmail_create_template` | 0 (SQLite) | No | Create a reusable email template in SQLite with `{{variable}}` placeholders. |
| `gmail_get_template` | 0 (SQLite) | Yes | Retrieve a stored template by `template_id` or `name`. |
| `gmail_list_templates` | 0 (SQLite) | Yes | List all stored email templates with variable schemas and timestamps. |
| `gmail_update_template` | 0 (SQLite) | No | Update a template's subject, plain-text body, HTML body, or description. |
| `gmail_delete_template` | 0 (SQLite) | No (Destructive) | Delete a template from SQLite storage. |

### Category F — Attachment Handling (3 Tools)
| Tool | Quota Units | Read-Only | Description |
| :--- | :---: | :---: | :--- |
| `gmail_list_attachments` | 5 | Yes | List all attachments on a message or thread with `attachment_id`, filename, MIME type, and byte size. |
| `gmail_download_attachment` | 5 | No | Download an attachment to a sanitized local path with path-traversal protection and collision handling. |
| `gmail_download_all_attachments` | 5 × N | No | Download all attachments from a message or thread (with optional filename/MIME filtering). |

### Category G — Label Management (6 Tools)
| Tool | Quota Units | Read-Only | Description |
| :--- | :---: | :---: | :--- |
| `gmail_list_labels` | 1 | Yes | List all system and custom labels in the mailbox. |
| `gmail_create_label` | 5 | No | Create a custom label (supports nested `Parent/Child` hierarchy and custom colors). |
| `gmail_update_label` | 5 | No | Rename a custom label or change its visibility/color settings (`SYSTEM_LABEL_IMMUTABLE` guard for system labels). |
| `gmail_delete_label` | 5 | No (Destructive) | Delete a custom label (`SYSTEM_LABEL_IMMUTABLE` guard for system labels). |
| `gmail_apply_labels` | 5 | No | Apply one or more labels to messages or threads. |
| `gmail_remove_labels` | 5 | No | Remove one or more labels from messages or threads. |

### Category H — Mailbox Organization & State Management (8 Tools)
| Tool | Quota Units | Read-Only | Description |
| :--- | :---: | :---: | :--- |
| `gmail_mark_read` | 5 | No | Remove the `UNREAD` label from messages or threads. |
| `gmail_mark_unread` | 5 | No | Apply the `UNREAD` label to messages or threads. |
| `gmail_star_messages` | 5 | No | Apply the `STARRED` label to messages or threads. |
| `gmail_unstar_messages` | 5 | No | Remove the `STARRED` label from messages or threads. |
| `gmail_archive_messages` | 5 | No | Archive messages or threads by removing the `INBOX` label. |
| `gmail_move_to_inbox` | 5 | No | Restore messages or threads to `INBOX`. |
| `gmail_trash_messages` | 5–50 | No (Destructive) | Move messages or threads to Trash (`messages.trash` / `threads.trash`). |
| `gmail_restore_from_trash` | 5 | No | Restore messages or threads from Trash (`messages.untrash` / `threads.untrash`). |

### Category I — Filters & Settings Management (4 Tools)
| Tool | Quota Units | Read-Only | Description |
| :--- | :---: | :---: | :--- |
| `gmail_list_filters` | 1 | Yes | List all server-side Gmail filter rules (`users.settings.filters.list`). |
| `gmail_create_filter` | 5 | No | Create a server-side Gmail filter rule (`from`, `to`, `subject`, `query`, `hasAttachment` → `addLabelIds`, `removeLabelIds`, `forward`). |
| `gmail_delete_filter` | 5 | No (Destructive) | Delete a server-side Gmail filter rule by `filter_id`. |
| `gmail_get_vacation_settings` / `gmail_update_vacation_settings` | 1 / 5 | Yes / No | Read or update out-of-office vacation responder settings (`users.settings.getVacation` / `updateVacation`). |

### Category J — Local Scheduling & Incremental Synchronization (5 Tools)
| Tool | Quota Units | Read-Only | Description |
| :--- | :---: | :---: | :--- |
| `gmail_schedule_send` | 0 (SQLite) | No | Schedule an email or template for future delivery in local SQLite (`scheduled_jobs`). |
| `gmail_list_scheduled_sends` | 0 (SQLite) | Yes | List scheduled send jobs filtered by status (`PENDING`, `IN_PROGRESS`, `SENT`, `FAILED`, `CANCELLED`). |
| `gmail_cancel_scheduled_send` | 0 (SQLite) | No | Cancel a `PENDING` scheduled send job before execution. |
| `gmail_process_due_scheduled_sends` | 100 × due | No | Execute all due scheduled jobs atomically (`PENDING -> IN_PROGRESS -> SENT/FAILED`). |
| `gmail_sync_mailbox` | 2 / page | No | Perform incremental synchronization via `users.history.list` from stored `startHistoryId`, falling back to full bootstrap on `HTTP 404`. |

---

## 2. Mandatory Sample Tool Invocations (17 Cases)

### 1. Searching Emails by Sender, Subject, Unread Status & Date Range
**Input:**
```json
{
  "tool": "gmail_search_messages",
  "arguments": {
    "sender": "recruiting@acme.com",
    "subject": "Interview",
    "is_unread": true,
    "after": "2026-10-01",
    "before": "2026-11-02",
    "max_results": 10
  }
}
```
**Output:**
```json
{
  "success": true,
  "status": "ok",
  "query": "from:recruiting@acme.com subject:\"Interview\" is:unread after:2026/10/01 before:2026/11/02",
  "messages": [
    {
      "id": "192e8c1a2b3c4d5e",
      "threadId": "192e8c1a2b3c4d5e",
      "snippet": "Hi Binit, we would love to schedule your technical interview...",
      "subject": "Interview Invitation - Senior AI Engineer",
      "from": "Recruiting Team <recruiting@acme.com>",
      "to": "user@example.com",
      "date": "Mon, 02 Nov 2026 09:30:00 +0530",
      "labelIds": ["INBOX", "UNREAD", "IMPORTANT"]
    }
  ],
  "count": 1,
  "next_page_token": null,
  "quota": { "consumed_units": 5 }
}
```

### 2. Fetching a Full Thread and Summarizing It
**Input:**
```json
{
  "tool": "gmail_get_thread_summary",
  "arguments": {
    "thread_id": "192e8c1a2b3c4d5e"
  }
}
```
**Output:**
```json
{
  "success": true,
  "status": "ok",
  "thread_id": "192e8c1a2b3c4d5e",
  "subject": "Interview Invitation - Senior AI Engineer",
  "message_count": 2,
  "participants": ["recruiting@acme.com", "user@example.com"],
  "first_message_date": "Mon, 02 Nov 2026 09:30:00 +0530",
  "last_message_date": "Mon, 02 Nov 2026 10:15:00 +0530",
  "last_sender": "recruiting@acme.com",
  "has_attachments": true,
  "attachments": [
    { "filename": "Job_Description.pdf", "mime_type": "application/pdf", "size": 148220 }
  ],
  "chronological_digest": [
    {
      "id": "192e8c1a2b3c4d5e",
      "from": "Recruiting Team <recruiting@acme.com>",
      "date": "Mon, 02 Nov 2026 09:30:00 +0530",
      "snippet": "Hi Binit, we would love to schedule your technical interview..."
    }
  ]
}
```

### 3. Creating a Single Draft with HTML Body and Multiple Attachments
**Input:**
```json
{
  "tool": "gmail_create_draft",
  "arguments": {
    "to": ["hiring@acme.com"],
    "cc": ["coordinator@acme.com"],
    "subject": "Application Documents - Binit",
    "body_text": "Please find attached my resume and cover letter.",
    "body_html": "<p>Hi Team,</p><p>Please find attached my <b>resume</b> and <b>cover letter</b>.</p>",
    "attachment_paths": [
      "./attachments/Resume.pdf",
      "./attachments/cover_letter_Acme.pdf"
    ]
  }
}
```
**Output:**
```json
{
  "success": true,
  "status": "created",
  "draft_id": "r-83920192837465",
  "message_id": "192e8d0011223344",
  "thread_id": "192e8d0011223344",
  "rfc822_message_id": "<f81d4fae-7dec-11d0-a765-00a0c91e6bf6@gmail-mcp.local>",
  "attachments_count": 2
}
```

### 4. Creating a Threaded Reply Draft
**Input:**
```json
{
  "tool": "gmail_create_reply_draft",
  "arguments": {
    "message_id": "192e8c1a2b3c4d5e",
    "body_text": "Thank you! Tuesday at 2:00 PM IST works great for me.",
    "reply_all": true
  }
}
```
**Output:**
```json
{
  "success": true,
  "status": "created",
  "draft_id": "r-9918273645",
  "message_id": "192e8d99887766",
  "thread_id": "192e8c1a2b3c4d5e",
  "subject": "Re: Interview Invitation - Senior AI Engineer",
  "in_reply_to": "<original-msg-id@acme.com>",
  "to": ["recruiting@acme.com"]
}
```

### 5. Sending a Single Email Directly
**Input:**
```json
{
  "tool": "gmail_send_email",
  "arguments": {
    "to": ["recruiting@acme.com"],
    "subject": "Availability Confirmation",
    "body_text": "Confirming my availability for Tuesday at 2:00 PM IST.",
    "confirm_send": true
  }
}
```
**Output:**
```json
{
  "success": true,
  "status": "sent",
  "message_id": "192e8e1122334455",
  "thread_id": "192e8e1122334455",
  "rfc822_message_id": "<9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d@gmail-mcp.local>",
  "daily_sends_used": 1,
  "daily_sends_remaining_hard_cap": 299
}
```

### 6. Replying to an Existing Thread (`gmail_reply_to_email`)
**Input:**
```json
{
  "tool": "gmail_reply_to_email",
  "arguments": {
    "message_id": "192e8c1a2b3c4d5e",
    "body_text": "Looking forward to speaking with the engineering panel on Tuesday!",
    "reply_all": true,
    "confirm_send": true
  }
}
```
**Output:**
```json
{
  "success": true,
  "status": "sent",
  "message_id": "192e8f5566778899",
  "thread_id": "192e8c1a2b3c4d5e",
  "subject": "Re: Interview Invitation - Senior AI Engineer",
  "in_reply_to": "<original-msg-id@acme.com>"
}
```

### 7. Creating a Reusable Template with Placeholders
**Input:**
```json
{
  "tool": "gmail_create_template",
  "arguments": {
    "name": "outreach_followup_v1",
    "subject_template": "Following up on {{role}} application at {{company}}",
    "body_text_template": "Hi {{first_name}},\n\nI wanted to follow up on my application for the {{role}} role at {{company}}.\n\nBest,\n{{sender_name}}",
    "body_html_template": "<p>Hi {{first_name}},</p><p>I wanted to follow up on my application for the <b>{{role}}</b> role at <b>{{company}}</b>.</p><p>Best,<br>{{sender_name}}</p>",
    "description": "Standard recruiter follow-up template"
  }
}
```
**Output:**
```json
{
  "success": true,
  "status": "created",
  "template": {
    "template_id": "tpl_4c8f9a1b2d3e",
    "name": "outreach_followup_v1",
    "variables": ["company", "first_name", "role", "sender_name"]
  }
}
```

### 8. Previewing a Template with Variable Substitution
**Input:**
```json
{
  "tool": "gmail_preview_template",
  "arguments": {
    "name": "outreach_followup_v1",
    "variables": {
      "first_name": "Priya",
      "role": "AI Engineer",
      "company": "Acme AI",
      "sender_name": "Binit"
    },
    "strict_variables": true
  }
}
```
**Output:**
```json
{
  "success": true,
  "status": "ok",
  "subject": "Following up on AI Engineer application at Acme AI",
  "body_text": "Hi Priya,\n\nI wanted to follow up on my application for the AI Engineer role at Acme AI.\n\nBest,\nBinit",
  "body_html": "<p>Hi Priya,</p><p>I wanted to follow up on my application for the <b>AI Engineer</b> role at <b>Acme AI</b>.</p><p>Best,<br>Binit</p>",
  "missing_variables": []
}
```

### 9. Bulk Draft Creation with Per-Recipient Variables (`gmail_bulk_create_drafts`)
**Input:**
```json
{
  "tool": "gmail_bulk_create_drafts",
  "arguments": {
    "template_name": "outreach_followup_v1",
    "items": [
      {
        "to": ["priya@acme.ai"],
        "variables": { "first_name": "Priya", "role": "AI Engineer", "company": "Acme AI", "sender_name": "Binit" }
      },
      {
        "to": ["rahul@novalabs.io"],
        "variables": { "first_name": "Rahul", "role": "ML Scientist", "company": "Nova Labs", "sender_name": "Binit" }
      }
    ],
    "dry_run": false
  }
}
```
**Output:**
```json
{
  "success": true,
  "status": "completed",
  "total_requested": 2,
  "succeeded_count": 2,
  "failed_count": 0,
  "results": [
    { "index": 0, "to": ["priya@acme.ai"], "success": true, "draft_id": "r-1001" },
    { "index": 1, "to": ["rahul@novalabs.io"], "success": true, "draft_id": "r-1002" }
  ]
}
```

### 10. Bulk Draft Creation with Attachments (`gmail_bulk_create_drafts_with_attachments`)
**Input:**
```json
{
  "tool": "gmail_bulk_create_drafts_with_attachments",
  "arguments": {
    "template_name": "outreach_followup_v1",
    "shared_attachment_paths": ["./attachments/Resume.pdf"],
    "items": [
      {
        "to": ["priya@acme.ai"],
        "variables": { "first_name": "Priya", "role": "AI Engineer", "company": "Acme AI", "sender_name": "Binit" },
        "attachment_paths": ["./attachments/cover_letter_Acme.pdf"]
      }
    ]
  }
}
```
**Output:**
```json
{
  "success": true,
  "status": "completed",
  "total_requested": 1,
  "succeeded_count": 1,
  "failed_count": 0,
  "results": [
    {
      "index": 0,
      "to": ["priya@acme.ai"],
      "success": true,
      "draft_id": "r-1003",
      "attachments_count": 2
    }
  ]
}
```

### 11. Bulk Sending with Pacing and Rate-Limit Controls (`gmail_bulk_send_emails`)
**Input:**
```json
{
  "tool": "gmail_bulk_send_emails",
  "arguments": {
    "template_name": "outreach_followup_v1",
    "items": [
      {
        "to": ["priya@acme.ai"],
        "variables": { "first_name": "Priya", "role": "AI Engineer", "company": "Acme AI", "sender_name": "Binit" }
      },
      {
        "to": ["rahul@novalabs.io"],
        "variables": { "first_name": "Rahul", "role": "ML Scientist", "company": "Nova Labs", "sender_name": "Binit" }
      }
    ],
    "delay_seconds": 1.5,
    "confirm_send": true
  }
}
```
**Output:**
```json
{
  "success": true,
  "status": "completed",
  "total_requested": 2,
  "succeeded_count": 2,
  "failed_count": 0,
  "results": [
    { "index": 0, "to": ["priya@acme.ai"], "success": true, "message_id": "192e9001" },
    { "index": 1, "to": ["rahul@novalabs.io"], "success": true, "message_id": "192e9002" }
  ],
  "daily_sends_used": 4
}
```

### 12. Downloading Attachments from a Message (`gmail_download_all_attachments`)
**Input:**
```json
{
  "tool": "gmail_download_all_attachments",
  "arguments": {
    "message_id": "192e8c1a2b3c4d5e",
    "destination_dir": "./data/downloads"
  }
}
```
**Output:**
```json
{
  "success": true,
  "status": "ok",
  "downloaded_count": 1,
  "files": [
    {
      "filename": "Job_Description.pdf",
      "saved_path": "./data/downloads/Job_Description.pdf",
      "size": 148220,
      "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    }
  ]
}
```

### 13. Creating and Applying Labels (`gmail_create_label` + `gmail_apply_labels`)
**Input:**
```json
{
  "tool": "gmail_create_label",
  "arguments": {
    "name": "Recruiting/Interviews",
    "label_list_visibility": "labelShow",
    "message_list_visibility": "show"
  }
}
```
**Output:**
```json
{
  "success": true,
  "status": "created",
  "label": {
    "id": "Label_42",
    "name": "Recruiting/Interviews",
    "type": "user"
  }
}
```

### 14. Batch Marking Messages as Read and Archiving Them
**Input:**
```json
{
  "tool": "gmail_archive_messages",
  "arguments": {
    "message_ids": ["192e8c1a2b3c4d5e", "192e8e1122334455"]
  }
}
```
**Output:**
```json
{
  "success": true,
  "status": "ok",
  "modified_count": 2,
  "removed_labels": ["INBOX"]
}
```

### 15. Creating a Gmail Filter Rule (`gmail_create_filter`)
**Input:**
```json
{
  "tool": "gmail_create_filter",
  "arguments": {
    "from_address": "recruiting@acme.com",
    "has_attachment": true,
    "add_label_ids": ["Label_42", "STARRED"],
    "mark_important": true
  }
}
```
**Output:**
```json
{
  "success": true,
  "status": "created",
  "filter": {
    "id": "ANe1Bmj928374",
    "criteria": { "from": "recruiting@acme.com", "hasAttachment": true },
    "action": { "addLabelIds": ["Label_42", "STARRED", "IMPORTANT"] }
  }
}
```

### 16. Scheduling an Email for Future Delivery (`gmail_schedule_send`)
**Input:**
```json
{
  "tool": "gmail_schedule_send",
  "arguments": {
    "recipient": "recruiting@acme.com",
    "scheduled_at": "2026-11-03T09:30:00+05:30",
    "subject": "Morning Follow-up",
    "body_text": "Good morning! Checking in on the interview schedule."
  }
}
```
**Output:**
```json
{
  "success": true,
  "status": "scheduled",
  "job_id": "sched_7f8e9d0c1b2a",
  "recipient": "recruiting@acme.com",
  "scheduled_at": "2026-11-03T09:30:00+05:30",
  "scheduled_epoch": 1793678400.0
}
```

### 17. Checking Quota Status (`gmail_get_quota_status`)
**Input:**
```json
{
  "tool": "gmail_get_quota_status",
  "arguments": {}
}
```
**Output:**
```json
{
  "success": true,
  "status": "ok",
  "quota": {
    "project_units_per_minute_limit": 1200000,
    "user_units_per_minute_limit": 6000,
    "effective_user_units_per_minute_limit": 4800,
    "current_minute_units_used": 215,
    "current_minute_units_remaining": 4585,
    "daily_sends_used": 4,
    "daily_send_soft_warning_limit": 200,
    "daily_send_hard_limit": 300,
    "personal_gmail_daily_ceiling": 500,
    "soft_warning_active": false,
    "hard_limit_reached": false
  }
}
```
