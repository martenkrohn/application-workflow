ALTER TABLE application_workspaces ADD COLUMN submission_route TEXT NOT NULL DEFAULT 'unknown'
    CHECK (submission_route IN ('portal', 'email', 'unknown'));

ALTER TABLE application_workspaces ADD COLUMN sender_email TEXT;

CREATE TABLE application_status_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id TEXT NOT NULL REFERENCES application_workspaces(application_id),
    group_id TEXT NOT NULL REFERENCES vacancy_groups(id),
    profile_id TEXT NOT NULL REFERENCES profile_versions(id),
    previous_status TEXT NOT NULL,
    new_status TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('manual', 'dashboard', 'email_assessment')),
    gmail_message_id TEXT,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_application_status_events_application
    ON application_status_events(application_id, created_at DESC, id DESC);

CREATE TABLE email_sync_state (
    account_address TEXT PRIMARY KEY,
    last_successful_scan_at TEXT,
    gmail_history_id TEXT,
    last_scan_result TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE application_emails (
    gmail_message_id TEXT PRIMARY KEY,
    gmail_thread_id TEXT NOT NULL,
    application_id TEXT REFERENCES application_workspaces(application_id),
    sender TEXT,
    recipients TEXT NOT NULL,
    subject TEXT,
    received_at TEXT,
    raw_artifact_path TEXT NOT NULL,
    raw_artifact_hash TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    routing_status TEXT NOT NULL
        CHECK (routing_status IN ('routed', 'unmatched', 'multiple', 'failed')),
    routing_reason TEXT NOT NULL
);

CREATE INDEX idx_application_emails_application_received
    ON application_emails(application_id, received_at DESC);
CREATE INDEX idx_application_emails_routing_received
    ON application_emails(routing_status, received_at DESC);
