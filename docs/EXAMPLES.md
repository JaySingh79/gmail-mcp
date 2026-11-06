# End-to-End Workflow Examples (`gmail_mcp`)

Practical multi-step automation recipes combining the Gmail MCP Server tools.

---

## Workflow 1: Personalized Hiring Manager Outreach Campaign (Safe Staged Drafts → Reviewed Send)

### Step 1: Create a Reusable Outreach Template
Call `gmail_create_template`:
```json
{
  "name": "ai_engineer_outreach_2026",
  "subject_template": "{{role}} Application — {{candidate_name}} ({{highlight}})",
  "body_text_template": "Hi {{manager_name}},\n\nI noticed {{company}} is scaling its {{team}} team and wanted to share my background in {{highlight}}.\n\nI have attached my resume and tailored cover letter for the {{role}} opening.\n\nBest regards,\n{{candidate_name}}",
  "body_html_template": "<p>Hi {{manager_name}},</p><p>I noticed <b>{{company}}</b> is scaling its {{team}} team and wanted to share my background in <b>{{highlight}}</b>.</p><p>I have attached my resume and tailored cover letter for the {{role}} opening.</p><p>Best regards,<br>{{candidate_name}}</p>"
}
```

### Step 2: Preview Variable Rendering Before Creating Drafts
Call `gmail_preview_template` with `strict_variables: true` to ensure no `{{placeholder}}` is missing:
```json
{
  "name": "ai_engineer_outreach_2026",
  "variables": {
    "manager_name": "Ananya",
    "company": "Sarvam AI",
    "team": "Foundational Models",
    "role": "Applied AI Engineer",
    "highlight": "Multi-Agent RAG & Voice LLM Pipelines",
    "candidate_name": "Binit"
  }
}
```

### Step 3: Bulk Create Personalized Drafts with Shared Resume + Custom Cover Letters
Call `gmail_bulk_create_drafts_with_attachments`:
```json
{
  "template_name": "ai_engineer_outreach_2026",
  "shared_attachment_paths": [
    "./attachments/Resume.pdf"
  ],
  "items": [
    {
      "to": ["ananya@sarvam.ai"],
      "variables": {
        "manager_name": "Ananya",
        "company": "Sarvam AI",
        "team": "Foundational Models",
        "role": "Applied AI Engineer",
        "highlight": "Multi-Agent RAG & Voice LLM Pipelines",
        "candidate_name": "Binit"
      },
      "attachment_paths": [
        "./attachments/cover_letter_Sarvam.pdf"
      ]
    }
  ]
}
```

### Step 4: Send Reviewed Draft
Once inspected in Gmail Drafts, call `gmail_send_draft`:
```json
{
  "draft_id": "r-1003",
  "confirm_send": true
}
```

---

## Workflow 2: Inbox Triage, Action Item Extraction & Automated Follow-Up Detection

### Step 1: Find Threads Where External Senders Are Waiting for a Reply
Call `gmail_find_unanswered_threads`:
```json
{
  "query": "label:INBOX -category:promotions -category:social",
  "max_results": 15
}
```

### Step 2: Summarize & Extract Action Items from High-Priority Threads
For each unanswered `thread_id`, call `gmail_extract_action_items`:
```json
{
  "thread_id": "192e8c1a2b3c4d5e"
}
```

### Step 3: Find Sent Threads Where the Recipient Has Not Responded After 4 Days
Call `gmail_find_threads_awaiting_followup`:
```json
{
  "min_days_elapsed": 4,
  "max_results": 20
}
```

### Step 4: Stage Threaded Follow-Up Reply Drafts
For each thread awaiting follow-up, call `gmail_create_reply_draft`:
```json
{
  "message_id": "192e8e1122334455",
  "body_text": "Hi Team,\n\nJust floating this to the top of your inbox in case you had any questions regarding my application.\n\nBest,\nBinit",
  "reply_all": true
}
```

---

## Workflow 3: Incremental Delta Synchronization (`users.history.list`)

### Step 1: Initial Sync Bootstrap
Call `gmail_sync_mailbox` with no arguments on first run to record the current mailbox `historyId` in SQLite (`sync_checkpoints`):
```json
{}
```

### Step 2: Poll Incremental Changes
Subsequent calls to `gmail_sync_mailbox` automatically read the stored `historyId` and return only delta changes (`messagesAdded`, `messagesDeleted`, `labelsAdded`, `labelsRemoved`) at **2 quota units per page**. If the `historyId` is expired (`HTTP 404`), the server automatically falls back to a clean full-sync bootstrap.
