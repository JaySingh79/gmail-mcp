# Authentication & OAuth 2.0 Guide (`docs/AUTHENTICATION.md`)

## 1. Account & Cloud Project Overview

- **Account Type**: Personal Gmail (`@gmail.com`)
- **Cloud Project Number**: `542762227233` (created before `2026-05-01`)
- **Authentication Method**: OAuth 2.0 Installed Application (Desktop App) Flow (`google_auth_oauthlib.flow.InstalledAppFlow`)
- **Service Accounts**: Not used. Google service accounts with domain-wide delegation only apply to Google Workspace domains and cannot impersonate personal `@gmail.com` accounts.

---

## 2. Required OAuth 2.0 Scopes

The server requests the narrowest practical scopes required for full mailbox organization and filter/settings management:

| Scope URI | Sensitivity | Purpose & Tools Requiring Scope |
| :--- | :--- | :--- |
| `https://www.googleapis.com/auth/gmail.modify` | Restricted | Read, search, compose, draft, send, reply, forward, label, archive, and move messages/threads to Trash (`gmail_search_messages`, `gmail_create_draft`, `gmail_send_email`, `gmail_modify_messages`, etc.). |
| `https://www.googleapis.com/auth/gmail.settings.basic` | Sensitive | Manage filters (`gmail_list_filters`, `gmail_get_filter`, `gmail_create_filter`, `gmail_delete_filter`), inspect send-as aliases, and read/update vacation responder settings (`gmail_get_mailbox_settings`, `gmail_update_vacation_settings`). |

> **Why `https://mail.google.com/` is NOT requested by default**:
> `https://mail.google.com/` grants unrestricted permanent deletion (`messages.delete` / `messages.batchDelete`). This server defaults all destructive actions to reversible `move_to_trash` (`messages.trash`), which only requires `gmail.modify`.

---

## 3. OAuth Consent Screen & Google Verification Notes

1. In **Google Cloud Console** (Project `542762227233`) → **APIs & Services** → **Enabled APIs & services**, ensure **Gmail API** is enabled.
2. Under **OAuth consent screen**:
   - **User Type**: External (for personal `@gmail.com` accounts).
   - **Publishing status**: While in **Testing** mode, add your personal Gmail address under **Test users**.
   - **Note on Testing vs Production**: Apps in *Testing* mode do not require Google security assessment for restricted scopes (`gmail.modify`), though Google may expire refresh tokens issued in Testing mode after 7 days unless the app is published or reauthorized.
3. Under **Credentials**:
   - Create an **OAuth client ID** of type **Desktop app**.
   - Download the JSON file and save it as `credentials.json` in the repository root (already listed in `.gitignore`).

---

## 4. Running Initial Authentication & Token Refresh

### Interactive Login
```powershell
uv run --no-project python -m src.scripts.gmail_mcp.auth.oauth --login
```
This opens a local loopback browser prompt (`access_type="offline"`, `prompt="consent"`), saves the resulting access and refresh tokens to `token.json` with owner-only file permissions, and validates authentication by calling `users.getProfile`.

### Force Reauthorization (After Revocation or Scope Changes)
```powershell
uv run --no-project python -m src.scripts.gmail_mcp.auth.oauth --login --force-reauth
```

### Check Status via CLI or MCP Tool
- **CLI**:
  ```powershell
  uv run --no-project python -m src.scripts.gmail_mcp.auth.oauth --status
  ```
- **MCP Tool**: Call `gmail_auth_status` with `{"verify_connectivity": true}`.

---

## 5. How `401` and `403` Errors Are Handled

- **`401 Unauthorized`**:
  - `TokenManager` automatically refreshes expired access tokens using the persisted `refresh_token`.
  - If the refresh token was revoked (`RefreshError`), the server returns `AUTH_REAUTHORIZATION_REQUIRED` with exact reauthorization instructions.
- **`403 Forbidden` (Never Confused with `401`)**:
  - `insufficientPermissions` / `accessNotConfigured`: The server inspects granted scopes and reports the exact missing scope, which tool requires it, why it is needed, and the 4-step reauthorization command — without retrying.
  - `userRateLimitExceeded` / `rateLimitExceeded`: Treated as a rate-limit condition and retried with truncated exponential backoff + jitter.
  - `dailyLimitExceeded`: Treated as an external daily sending limit lockout and halted immediately without retrying.
