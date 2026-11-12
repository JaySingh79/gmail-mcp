---
name: gmail-outreach-templates
description: >-
  Use when designing, creating, previewing, or staging single or bulk email outreach templates and drafts for job applications or partner pitches across one or multiple companies using Gmail MCP.
metadata:
  category: workflow
  triggers:
    - outreach template
    - job application template
    - company placeholder
    - create outreach draft
    - bulk email drafts
    - multi company outreach
---

# Gmail Outreach & Job Application Templating Skill

A specialized workflow guide for Antigravity (`agy`) and Claude Code agents to reliably build, manage, and execute single or bulk company outreach campaigns using `gmail_mcp`.

---

## 1. Core Architectural Mental Model

Before touching tools, understand how `gmail_mcp` separates template logic from draft staging:

```
┌────────────────────────────────────────────────────────┐
│               Local SQLite Engine                      │
│ (data/gmail_mcp.db -> email_templates table)           │
│                                                        │
│  Stores:                                               │
│   • template_id (e.g. 'job_application_data_science') │
│   • Subject & Body with {{company}}, {{name}}          │
│   • Permanent attachments (e.g. Master Resume PDF)     │
└──────────────────────────┬─────────────────────────────┘
                           │
             Referenced by │ template_id
                           ▼
┌────────────────────────────────────────────────────────┐
│                 Gmail API Drafts                       │
│                                                        │
│  Single: gmail_create_draft                            │
│  Bulk:   gmail_bulk_create_drafts                      │
│                                                        │
│  Dynamically combines:                                 │
│   1. Template subject + bodies (variables injected)    │
│   2. Template permanent attachments (Master Resume)    │
│   3. Per-company custom files (Company Cover Letter)   │
└────────────────────────────────────────────────────────┘
```

### The Two Golden Rules
1. **Never Hardcode Company Names**: Global templates must use `{{company}}` and fallback placeholders like `{{recipient_name|Hiring Manager}}`.
2. **Isolate Company-Specific Documents**: Master resumes belong in the template's permanent `attachments` list. Company-specific cover letters (`cover_letter_<Company>.pdf`) must **never** be hardcoded into the global template; inject them during draft creation.

---

## 2. When to Use This Skill

- Creating reusable outreach or application templates with variable placeholders (`{{company}}`, `{{role}}`, `{{recipient_name}}`).
- Staging an application draft for a single target company with a customized cover letter and master resume.
- Generating batches of 2 to 50+ personalized outreach drafts across multiple companies simultaneously.
- Updating or versioning existing templates when subject lines or body copy change.

---

## 3. Decision Matrix: Choosing the Right Flow

| Scenario | Primary MCP Tool | Key Input Parameters |
| :--- | :--- | :--- |
| **Create new template** | `gmail_create_template` | `name`, `template_id`, `subject`, `body_text`, `body_html`, `attachments` (resume) |
| **Verify template rendering** | `gmail_preview_template` | `template_id`, `variables: {"company": "Acme"}` |
| **Stage 1 company draft** | `gmail_create_draft` | `to`, `template_id`, `variables: {"company": ...}`, `attachments: [cover_letter]` |
| **Stage 2+ company drafts** | `gmail_bulk_create_drafts` | `template_id`, `recipients: [{to, company, attachments: [...]}]` |
| **Update template wording** | `gmail_update_template` | `template_id`, updated fields (`subject`, `body_text`, etc.) |

---

## 4. Execution Workflows

### Workflow A: Creating or Inspecting a Reusable Template

#### Step 1: Discover or Check Existing Templates
Always inspect existing templates first to avoid duplicate IDs:
```json
// Tool: gmail_list_templates
{}
```

#### Step 2: Ensure Master Resume is in Allowed Directory
All attachments must reside within project allowed paths (e.g. `D:\Projects\gmail_mcp\attachments\` or `data/downloads/`). If retrieving from a previous email, use `gmail_get_attachment`.

#### Step 3: Create the Template
Placeholders must use double curly braces: `{{variable}}` or `{{variable|default_fallback}}`.
```json
// Tool: gmail_create_template
{
  "template_id": "job_application_data_science",
  "name": "Job Application - DS/AI/ML",
  "subject": "Application for DS/AI/ML Full Time Opportunities at {{company}}",
  "body_text": "Hello {{recipient_name|Hiring Manager}},\n\nMy name is Jay Singh. I am keen to join for full time roles in Data Science/AI/ML at {{company}}...\n\nThank you and Regards,",
  "body_html": "<p>Hello <b>{{recipient_name|Hiring Manager}}</b>,<br><br>My name is <b>Jay Singh</b>. I am keen to join for full time roles in <b>Data Science/AI/ML</b> at <b>{{company}}</b>...</p>",
  "variables": ["company", "recipient_name"],
  "defaults": {
    "recipient_name": "Hiring Manager"
  },
  "attachments": [
    "D:\\Projects\\gmail_mcp\\attachments\\Resume ML_Vision_9_Nov.pdf"
  ]
}
```

#### Step 4: Preview to Verify Placeholder Substitution
```json
// Tool: gmail_preview_template
{
  "template_id": "job_application_data_science",
  "variables": {
    "company": "DeepMind",
    "recipient_name": "Demis"
  }
}
```

---

### Workflow B: Staging a Single Company Application Draft

When applying to a single company, use `gmail_create_draft`. You only specify `to`, `template_id`, `variables`, and any company-specific cover letter file:

```json
// Tool: gmail_create_draft
{
  "to": "recruiter@anthropic.com",
  "template_id": "job_application_data_science",
  "variables": {
    "company": "Anthropic",
    "recipient_name": "Hiring Team"
  },
  "attachments": [
    "D:\\Projects\\gmail_mcp\\attachments\\cover_letter_Anthropic.pdf"
  ]
}
```

> [!TIP]
> **Attachment Merging**: `gmail_mcp` will automatically attach both:
> 1. `cover_letter_Anthropic.pdf` (from the draft call)
> 2. `Resume ML_Vision_9_Nov.pdf` (inherited from the template)

---

### Workflow C: Staging Multi-Company Application Drafts in Bulk

When applying to multiple companies at once (e.g. 5, 10, or 30 companies), use `gmail_bulk_create_drafts`.

Pass a single `template_id` alongside a list of recipient objects. Each recipient object can specify:
- `to`: Target email address
- `company`: Target company name (substituted into `{{company}}`)
- `recipient_name`: (Optional) Recruiter or hiring manager name
- `attachments`: List of per-company attachments (e.g. custom cover letter for that specific company)

```json
// Tool: gmail_bulk_create_drafts
{
  "template_id": "job_application_data_science",
  "recipients": [
    {
      "to": "talent@apple.com",
      "company": "Apple",
      "recipient_name": "AI Talent Acquisition",
      "attachments": [
        "D:\\Projects\\gmail_mcp\\attachments\\cover_letter_Apple.pdf"
      ]
    },
    {
      "to": "careers@nvidia.com",
      "company": "NVIDIA",
      "recipient_name": "Jensen",
      "attachments": [
        "D:\\Projects\\gmail_mcp\\attachments\\cover_letter_NVIDIA.pdf"
      ]
    },
    {
      "to": "hiring@meta.com",
      "company": "Meta",
      "recipient_name": "FAIR Recruiter",
      "attachments": [
        "D:\\Projects\\gmail_mcp\\attachments\\cover_letter_Meta.pdf"
      ]
    }
  ]
}
```

**What Happens Behind the Scenes:**
- For Apple: Creates Draft with Subject `Application for DS/AI/ML Full Time Opportunities at Apple`, body mentioning Apple, and files `[cover_letter_Apple.pdf, Resume ML_Vision_9_Nov.pdf]`.
- For NVIDIA: Creates Draft with Subject `Application for DS/AI/ML Full Time Opportunities at NVIDIA`, body mentioning NVIDIA, and files `[cover_letter_NVIDIA.pdf, Resume ML_Vision_9_Nov.pdf]`.
- For Meta: Creates Draft with Subject `Application for DS/AI/ML Full Time Opportunities at Meta`, body mentioning Meta, and files `[cover_letter_Meta.pdf, Resume ML_Vision_9_Nov.pdf]`.

---

## 5. Modifying or Iterating on Templates

To update the copy, subject, or default variables of an existing template without losing version history:

```json
// Tool: gmail_update_template
{
  "template_id": "job_application_data_science",
  "subject": "Application for Senior DS/AI/ML Opportunities at {{company}}"
}
```
The server automatically increments the `version` counter and timestamps the update.

---

## 6. Critical Safety Guardrails & Anti-Rationalization

> [!CAUTION]
> **Draft First, Never Auto-Send**:
> Never call `gmail_send_email`, `gmail_execute_bulk_send`, or `gmail_send_reply` without explicit user permission. Always stage outreach as **drafts** (`gmail_create_draft` or `gmail_bulk_create_drafts`) so the user can review them in Gmail before sending.

- **Check Recipient Duplication**: Ensure recipient lists do not contain duplicate emails before executing bulk operations.
- **Verify Local Paths**: Ensure all attachment paths resolve cleanly and exist on disk before invoking draft tools.
- **Quota Consciousness**:
  - `drafts.create` costs **10 API quota units**.
  - Creating 20 drafts consumes 200 units (well within personal Gmail's 6,000 units/min cap).
  - Always report created draft IDs to the user.
