PRAGMA foreign_keys = ON;

CREATE TABLE collection_runs (
    id TEXT PRIMARY KEY,
    command_version TEXT NOT NULL,
    configuration_fingerprint TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('created', 'running', 'succeeded', 'partial', 'failed', 'cancelled')),
    summary_path TEXT
);

CREATE TABLE source_attempts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES collection_runs(id),
    source_name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    observations_created INTEGER NOT NULL DEFAULT 0,
    observations_unchanged INTEGER NOT NULL DEFAULT 0,
    artifacts_created INTEGER NOT NULL DEFAULT 0,
    artifacts_unchanged INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT
);

CREATE TABLE raw_artifacts (
    content_hash TEXT PRIMARY KEY,
    relative_path TEXT NOT NULL UNIQUE,
    size INTEGER NOT NULL CHECK (size >= 0),
    media_type TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE artifact_retrievals (
    id TEXT PRIMARY KEY,
    artifact_hash TEXT NOT NULL REFERENCES raw_artifacts(content_hash),
    source_name TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES collection_runs(id),
    retrieved_at TEXT NOT NULL,
    source_url TEXT NOT NULL
);

CREATE TABLE source_observations (
    id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    external_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    artifact_hash TEXT NOT NULL REFERENCES raw_artifacts(content_hash),
    source_url TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_run_id TEXT NOT NULL REFERENCES collection_runs(id),
    UNIQUE (source_name, external_id, content_hash)
);

CREATE INDEX idx_source_attempts_run ON source_attempts(run_id);
CREATE INDEX idx_observations_identity ON source_observations(source_name, external_id);
