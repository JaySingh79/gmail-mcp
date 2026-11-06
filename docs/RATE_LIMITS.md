# Quota & Rate-Limit Management (`docs/RATE_LIMITS.md`)

## 1. Documented Google Gmail REST API v1 Quota Model

| Limit | Standard Allocation | Effective Server Cap (with 20% Safety Margin) |
| :--- | ---: | ---: |
| Per project per minute | `1,200,000` units | `960,000` units/min |
| Per user per project per minute | `6,000` units | `4,800` units/min |
| Per project daily billing threshold | `80,000,000` units | `64,000,000` units/day |

### Cloud Project `542762227233` Verification Note
Although Cloud project `542762227233` was created before `2026-05-01`, legacy quota retention requires active Gmail API traffic between November 2025 and April 2026 and verification in Google Cloud Console (**IAM & Admin → Quotas & System Limits**). This server **never assumes legacy quotas** and enforces the standard `6,000` units/user/minute model by default (`legacy_quota_verified=False`).

---

## 2. Authoritative Method Quota Unit Costs

Configured in `QUOTA_COST_REGISTRY` (`src/scripts/gmail_mcp/config.py`):

| API Method | Quota Units |
| :--- | ---: |
| `users.getProfile` | 1 |
| `messages.list` | 5 |
| `messages.get` (`metadata` / `minimal`) | 20 |
| `messages.get` (`full` / `raw` / `html`) | 60 |
| `messages.send` | 100 |
| `messages.modify` / `messages.trash` / `messages.untrash` | 5 |
| `messages.delete` | 10 |
| `messages.batchModify` / `messages.batchDelete` | 50 |
| `messages.attachments.get` | 20 |
| `drafts.create` / `drafts.delete` | 10 |
| `drafts.get` (`minimal` / `metadata`) | 20 |
| `drafts.get` (`full` / `raw` / `html`) | 60 |
| `drafts.list` | 5 |
| `drafts.update` | 15 |
| `drafts.send` | 100 |
| `threads.list` / `threads.modify` / `threads.trash` | 10 |
| `threads.get` (`minimal` / `metadata`) | 40 |
| `threads.get` (`full`) | 120 |
| `history.list` | 2 |
| `watch` | 100 |
| `stop` | 50 |
| `labels.list` / `labels.get` | 1 |
| `labels.create` / `labels.update` / `labels.delete` | 5 |
| `settings.filters.list` / `settings.filters.get` | 1 |
| `settings.filters.create` / `settings.filters.delete` | 5 |

---

## 3. Personal Gmail Daily Sending Safeguards

Google enforces an external ceiling of **500 emails/recipients per rolling 24 hours** for personal Gmail accounts (shared across Gmail Web, Mobile, REST API, and SMTP) and **500 recipients per individual message**.

Because Gmail REST API does **not** expose a live "remaining daily sends" counter endpoint, this server enforces conservative application-level safeguards:

```python
GMAIL_CONFIG = {
    "account_type": "personal",
    "daily_send_soft_cap": 200,
    "daily_send_hard_cap": 300,
    "send_concurrency": 1,
    "send_interval_seconds": 3,
    "read_concurrency": 3,
    "write_concurrency": 2,
    "batch_size": 25,
    "max_api_batch_size": 50,
    "max_retries": 5,
    "max_backoff_seconds": 64,
    "search_page_size": 500,
    "attachment_warning_size_mb": 18,
    "attachment_max_size_mb": 25,
    "quota_safety_margin": 0.20,
}
```

### How Daily Sending Protection Works
1. Every outgoing message and reply is recorded in the SQLite `send_ledger` table with its total recipient count (`To + Cc + Bcc`).
2. When `reconcile_sent_mail=True` is requested (or via `gmail_get_runtime_limits`), the server queries `in:sent` over the past 24 hours to account for messages sent outside the MCP server.
3. Available headroom is calculated against `max(local_ledger_24h, reconciled_sent_24h)`.
4. Crossing `daily_send_soft_cap` (`200`) attaches an explicit warning; reaching `daily_send_hard_cap` (`300`) immediately blocks or pauses execution with `error_code="DAILY_SEND_LIMIT"` and `resumable=True`.

---

## 4. Error Classification, Backoff & Ambiguous Send Reconciliation

- **Backoff Formula**:
  ```python
  delay = min(2 ** attempt + random.uniform(0, 1), 64)
  ```
  Honours `Retry-After` headers on `429` responses when present.
- **Ambiguous Send Timeout Rule**:
  If `messages.send` experiences a network timeout or `5xx` error, `RetryManager` **never** automatically resends the email. Instead, it searches `rfc822msgid:<Message-ID>` to verify whether the message landed in Sent mail. If found, it reconciles the operation as `SENT`; if not determinable, it marks the ledger entry `INDETERMINATE` and halts to prevent duplicate emails.
