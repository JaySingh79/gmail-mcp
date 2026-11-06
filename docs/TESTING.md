# Testing Guide (`docs/TESTING.md`)

## 1. Three-Layer Testing Architecture

The Gmail Automation MCP Server includes three independent testing layers plus 10 mandatory stress tests:

1. **Layer 1 — Unit & Security Tests (`tests/unit/`, `tests/security/`, `tests/workflows/`)**:
   - Uses `MockGmailBackend` and in-memory/temporary SQLite databases.
   - Zero live network calls or real emails sent.
2. **Layer 2 — Gated Live Gmail Integration Tests (`tests/integration/test_live_gmail.py`)**:
   - Disabled by default (`GMAIL_E2E_ENABLED=0`, `GMAIL_E2E_SEND_ENABLED=0`).
   - When enabled, tests live read-only profile/labels/search and draft creation/deletion, and restricts any single-send test to `GMAIL_E2E_ALLOWED_RECIPIENTS`.
   - Never performs live bulk sending during automated test runs.
3. **Layer 3 — MCP Protocol Tests (`tests/integration/test_mcp_protocol.py`)**:
   - Validates server startup, tool discovery across all 57 tools, parameter schemas, `ToolAnnotations`, structured output contracts, error propagation, concurrent tool invocations, and real STDIO subprocess JSON-RPC communication via `mcp.client.stdio.stdio_client`.

---

## 2. Commands to Run Tests

### Run All Unit, Security, Stress & MCP Protocol Tests
```powershell
uv run --no-project python -m pytest src/scripts/gmail_mcp/tests -v
```

### Run Only Layer 1 Unit & Feature Matrix Tests
```powershell
uv run --no-project python -m pytest src/scripts/gmail_mcp/tests/unit -v
```

### Run Only Security Safeguard Tests
```powershell
uv run --no-project python -m pytest src/scripts/gmail_mcp/tests/security -v
```

### Run Only the 10 Mandatory Stress Tests
```powershell
uv run --no-project python -m pytest src/scripts/gmail_mcp/tests/workflows/test_stress_workflows.py -v
```

### Run Only Layer 3 MCP Protocol & STDIO Handshake Tests
```powershell
uv run --no-project python -m pytest src/scripts/gmail_mcp/tests/integration/test_mcp_protocol.py -v
```

### Run Optional Layer 2 Live Gmail Read/Draft E2E Tests
```powershell
$env:GMAIL_E2E_ENABLED="1"
$env:GMAIL_E2E_SEND_ENABLED="0"
uv run --no-project python -m pytest src/scripts/gmail_mcp/tests/integration/test_live_gmail.py -v
```
