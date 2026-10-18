CREATE TABLE morning_runs (
    id TEXT PRIMARY KEY,
    context_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('active', 'complete', 'superseded')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    progress_json TEXT NOT NULL
);

CREATE TABLE problem_batches (
    batch_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('screening', 'email')),
    morning_id TEXT REFERENCES morning_runs(id),
    created_at TEXT NOT NULL,
    directory TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE problem_items (
    work_item_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES problem_batches(batch_id),
    observation_id TEXT REFERENCES source_observations(id),
    version_id TEXT,
    gmail_message_id TEXT REFERENCES application_emails(gmail_message_id),
    state TEXT NOT NULL CHECK (state IN ('open', 'resolved', 'released'))
);
CREATE INDEX idx_problem_items_observation ON problem_items(observation_id, state);
CREATE INDEX idx_problem_items_version ON problem_items(version_id, state);
CREATE INDEX idx_problem_items_email ON problem_items(gmail_message_id, state);
