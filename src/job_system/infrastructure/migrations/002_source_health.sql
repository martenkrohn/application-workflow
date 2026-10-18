ALTER TABLE source_attempts ADD COLUMN checkpoint TEXT;

CREATE TABLE source_health (
    source_name TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('healthy', 'degraded')),
    last_attempt_at TEXT NOT NULL,
    last_success_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    last_item_count INTEGER NOT NULL DEFAULT 0 CHECK (last_item_count >= 0),
    last_error_code TEXT,
    last_error_message TEXT,
    checkpoint TEXT
);
