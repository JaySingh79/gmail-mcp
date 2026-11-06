"""Initial SQLite schema migration (v001) with indexes for all persistent subsystems."""

MIGRATION_VERSION = 1
MIGRATION_NAME = "v001_initial_schema"

UP_SQL = """
-- 1. Templates & Template Versions
CREATE TABLE IF NOT EXISTS templates (
    template_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    subject TEXT NOT NULL,
    body_text TEXT,
    body_html TEXT,
    variables_json TEXT NOT NULL DEFAULT '[]',
    defaults_json TEXT NOT NULL DEFAULT '{}',
    attachments_json TEXT NOT NULL DEFAULT '[]',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_templates_name ON templates(name);

CREATE TABLE IF NOT EXISTS template_versions (
    template_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    name TEXT NOT NULL,
    subject TEXT NOT NULL,
    body_text TEXT,
    body_html TEXT,
    variables_json TEXT NOT NULL DEFAULT '[]',
    defaults_json TEXT NOT NULL DEFAULT '{}',
    attachments_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    PRIMARY KEY (template_id, version),
    FOREIGN KEY (template_id) REFERENCES templates(template_id) ON DELETE CASCADE
);

-- 2. Two-Phase Bulk Operation Plans (Send & Reply)
CREATE TABLE IF NOT EXISTS bulk_plans (
    plan_id TEXT PRIMARY KEY,
    plan_type TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    confirmation_phrase TEXT NOT NULL,
    requested_count INTEGER NOT NULL,
    total_recipient_count INTEGER NOT NULL,
    estimated_quota_units INTEGER NOT NULL,
    items_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PREPARED',
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    executed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_bulk_plans_status ON bulk_plans(status);

-- 3. Auditable Sending Ledger (Idempotency & Daily Cap Enforcement)
CREATE TABLE IF NOT EXISTS send_ledger (
    operation_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    plan_id TEXT,
    operation_type TEXT NOT NULL,
    recipient TEXT NOT NULL,
    cc_count INTEGER NOT NULL DEFAULT 0,
    bcc_count INTEGER NOT NULL DEFAULT 0,
    total_recipient_count INTEGER NOT NULL DEFAULT 1,
    thread_id TEXT,
    gmail_message_id TEXT,
    rfc_message_id TEXT,
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    quota_cost INTEGER NOT NULL DEFAULT 100,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    epoch_seconds REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_send_ledger_epoch ON send_ledger(epoch_seconds, status);
CREATE INDEX IF NOT EXISTS idx_send_ledger_plan_id ON send_ledger(plan_id);
CREATE INDEX IF NOT EXISTS idx_send_ledger_rfc_msgid ON send_ledger(rfc_message_id);

-- 4. Persistent Scheduled Jobs
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    job_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    scheduled_at TEXT NOT NULL,
    scheduled_epoch REAL NOT NULL,
    template_id TEXT,
    recipient TEXT NOT NULL,
    cc_json TEXT NOT NULL DEFAULT '[]',
    bcc_json TEXT NOT NULL DEFAULT '[]',
    reply_to TEXT,
    subject TEXT,
    body_text TEXT,
    body_html TEXT,
    variables_json TEXT NOT NULL DEFAULT '{}',
    attachments_json TEXT NOT NULL DEFAULT '[]',
    thread_id TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    gmail_message_id TEXT,
    last_result_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_due ON scheduled_jobs(status, scheduled_epoch);

-- 5. Incremental Synchronization State & Deduplicated Notifications
CREATE TABLE IF NOT EXISTS sync_state (
    account_key TEXT PRIMARY KEY,
    email_address TEXT,
    history_id TEXT NOT NULL,
    watch_expiration TEXT,
    topic_name TEXT,
    last_sync_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processed_notifications (
    notification_id TEXT PRIMARY KEY,
    history_id TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    epoch_seconds REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_processed_notifications_epoch ON processed_notifications(epoch_seconds);

-- 6. Cross-Process Shared Quota Window Ledger
CREATE TABLE IF NOT EXISTS quota_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket_scope TEXT NOT NULL,
    method_name TEXT NOT NULL,
    units INTEGER NOT NULL,
    epoch_seconds REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_quota_events_window ON quota_events(bucket_scope, epoch_seconds);
"""
