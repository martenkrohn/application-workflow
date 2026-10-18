CREATE TABLE job_alert_messages (
    source_name TEXT NOT NULL,
    message_id TEXT NOT NULL,
    raw_hash TEXT NOT NULL,
    received_at TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    PRIMARY KEY (source_name, message_id)
);
CREATE TABLE job_board_links (
    source_name TEXT NOT NULL,
    link_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    vacancy_url TEXT NOT NULL,
    title TEXT NOT NULL,
    message_id TEXT NOT NULL,
    email_hash TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending','captured','blocked','unavailable','failed')),
    resolved_id TEXT,
    detail_hash TEXT,
    last_error TEXT,
    PRIMARY KEY (source_name, link_id)
);
CREATE TABLE job_board_sync (
    source_name TEXT PRIMARY KEY,
    last_scan_at TEXT NOT NULL
);
CREATE TABLE source_identity_aliases (
    source_name TEXT NOT NULL,
    alias TEXT NOT NULL,
    target TEXT NOT NULL,
    PRIMARY KEY (source_name, alias)
);
