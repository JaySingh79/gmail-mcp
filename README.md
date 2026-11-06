# Production-Grade Gmail Automation MCP Server

A modular, quota-aware Model Context Protocol (MCP) server for personal and Workspace Gmail accounts built on the official **Gmail REST API v1** and the official **MCP Python SDK (`mcp 1.29.0`)**.

---

## Key Capabilities

- **57 MCP Tools across 10 Domains**: Account diagnostics, low-latency server-side search, threads, drafts, MIME-preserving draft duplication (`1..100+`), bulk personalized draft creation (`100+`), bulk draft attachments (`100+`), single & two-phase confirmed bulk sending (`CONFIRM SEND N EMAILS`), threaded bulk replies (`CONFIRM SEND N REPLIES`), SQLite templates with HTML escaping, labels (`batchModify` up to 1,000 messages), filters, mailbox organization, persistent SQLite scheduled sending, incremental History API synchronization, and JSON/CSV/EML exports.
- **Official Gmail REST API v1**: Uses `google-api-python-client` directly; zero dependency on `gmailmcp.googleapis.com` or Google Workspace admin APIs.
- **Centralized Quota & Rate-Limit Protection**:
  - Enforces per-user (`6,000` units/min standard, `4,800` effective with 20% safety margin) and per-project (`1,200,000` units/min) weighted token buckets backed by SQLite.
  - Enforces conservative personal Gmail sending safeguards (`daily_send_soft_cap=200`, `daily_send_hard_cap=300` vs external `500/day` Google account limit) and reconciles against `in:sent`.
- **Ambiguous Send Timeout Reconciliation**:
  - Assigns deterministic RFC-2822 `Message-ID` headers (`<...@gmail-mcp.local>`) before sending and reconciles via `rfc822msgid:` search after any timeout — **never blindly resends** an email after an ambiguous timeout.

---

## Directory Layout

```text
gmail_mcp/
├── src/gmail_mcp/
│   ├── server.py              # FastMCP server registration & CLI entrypoint
│   ├── config.py              # GmailConfig & authoritative QUOTA_COST_REGISTRY
│   ├── auth/
│   │   ├── credentials.py     # OAuth 2.0 scope definitions & missing-scope diagnostics
│   │   ├── token_manager.py   # Secure token persistence, auto-refresh & status reports
│   │   └── oauth.py           # InstalledAppFlow loopback login & users.getProfile check
│   ├── core/
│   │   ├── gmail_client.py    # Shared dependency-injected GmailClientWrapper
│   │   ├── quota_manager.py   # Method unit costs, rolling windows & 24h send headroom
│   │   ├── rate_limiter.py    # Weighted token buckets & read/write/send concurrency
│   │   ├── retry_manager.py   # HTTP 400/401/403/404/429/5xx classifier & send reconciler
│   │   ├── mime_builder.py    # EmailMessage builder, raw MIME parser & reconstructor
│   │   ├── pagination.py      # Server-side query builder & nextPageToken collector
│   │   └── safety.py          # Path traversal, symlink, size, HTML escape & log redaction
│   ├── storage/
│   │   ├── database.py        # SQLite WAL connection & non-destructive migration manager
│   │   ├── models.py          # Pydantic persistence models
│   │   ├── repositories.py    # Transactional repositories (templates, ledger, plans, jobs, sync)
│   │   └── migrations/
│   │       └── v001_initial.py# Initial SQLite schema & indexes
│   └── tools/
│       ├── account.py         # Profile, auth status, runtime limits, health, mailbox stats
│       ├── search.py          # Search messages, search threads, list unread, export
│       ├── messages.py        # Get individual message (minimal, metadata, full, raw)
│       ├── threads.py         # Get full conversation thread
│       ├── drafts.py          # Create, get, list, duplicate, update, delete drafts
│       ├── bulk_operations.py # Bulk create 100+ drafts & bulk attach files to 100+ drafts
│       ├── sending.py         # Single send, reply, reply-all, forward, 2-phase bulk send/reply
│       ├── templates.py       # SQLite templates CRUD, versioning & HTML-safe preview
│       ├── attachments.py     # List attachments, download attachments, build MIME attachments
│       ├── labels.py          # List, create, update, delete, apply, remove & batchModify labels
│       ├── mailbox.py         # Unified gmail_modify_messages & vacation/alias settings
│       ├── filters.py         # List, get, create & delete Gmail filters
│       ├── scheduling.py      # Persistent SQLite scheduled sends & background worker
│       └── synchronization.py # Pub/Sub watch, stop & incremental history.list sync
├── tests/
│   ├── conftest.py            # Stateful MockGmailBackend & isolated fixtures
│   ├── unit/                  # Layer 1 unit & feature-matrix tests
│   ├── security/              # Filesystem, recipient, BCC & destructive safeguard tests
│   ├── workflows/             # 10 mandatory stress tests (100 drafts, 1,000 unread, etc.)
│   └── integration/           # Layer 2 gated live E2E & Layer 3 MCP STDIO protocol tests
├── docs/
│   ├── TOOL_REFERENCE.md      # Complete tool reference & 17+ JSON examples
│   ├── AUTHENTICATION.md      # Personal Gmail OAuth 2.0 setup & troubleshooting
│   ├── RATE_LIMITS.md         # Quota model, unit costs & daily sending safeguards
│   ├── EXAMPLES.md            # Multi-step workflow guides
│   └── TESTING.md             # Test execution guide
├── pyproject.toml             # Package metadata, dependencies, scripts, pytest configuration
├── requirements.txt           # Standard pip requirements
├── main.py                    # Direct CLI execution script
└── .env.example               # Template environment configuration
```

---

## Quickstart

### 1. Install Dependencies

Using `uv` (recommended):
```bash
uv venv
uv pip install -e .
```

Or using standard `pip`:
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e .
```

### 2. Authenticate Your Gmail Account
Ensure your Desktop App OAuth 2.0 client JSON from Google Cloud Console is saved at `./credentials.json` (or set via `GMAIL_CREDENTIALS_PATH`), then run:
```bash
python main.py --auth
```
To inspect token status at any time:
```bash
python -m gmail_mcp.auth.oauth --status
```

### 3. Verify Server Self-Check
```bash
python main.py --self-check
```
Or if installed as a package:
```bash
gmail-mcp --self-check
```

### 4. Start the MCP Server
```bash
python main.py --transport stdio
```

### 5. MCP Client Configuration (`.mcp.json` or `claude_desktop_config.json`)
```json
{
  "mcpServers": {
    "gmail": {
      "command": "uv",
      "args": [
        "--directory",
        "D:\\Projects\\gmail_mcp",
        "run",
        "gmail-mcp"
      ],
      "env": {
        "GMAIL_CREDENTIALS_PATH": "D:\\Projects\\gmail_mcp\\credentials.json",
        "GMAIL_TOKEN_PATH": "D:\\Projects\\gmail_mcp\\token.json"
      }
    }
  }
}
```

### 6. Run Test Suites
```bash
pytest tests -v
```

---

## Documentation

- [`TOOL_REFERENCE.md`](docs/TOOL_REFERENCE.md) — Exhaustive specification for all 57 tools and 17 concrete sample invocations.
- [`AUTHENTICATION.md`](docs/AUTHENTICATION.md) — Personal Gmail OAuth 2.0 setup, scope reference, and token lifecycle.
- [`RATE_LIMITS.md`](docs/RATE_LIMITS.md) — Quota model, unit costs, and daily sending safeguards.
- [`EXAMPLES.md`](docs/EXAMPLES.md) — Multi-step automation recipes (outreach, triage, sync).
- [`TESTING.md`](docs/TESTING.md) — Test execution guide across all 3 testing layers and stress tests.

---

## License

MIT
