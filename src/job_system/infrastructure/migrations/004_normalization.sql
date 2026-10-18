CREATE TABLE ai_work_batches (
    id TEXT PRIMARY KEY,
    prompt_id TEXT NOT NULL,
    prompt_version INTEGER NOT NULL,
    manifest_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('prepared', 'validated', 'partial', 'imported')),
    input_directory TEXT NOT NULL,
    output_path TEXT NOT NULL,
    item_count INTEGER NOT NULL CHECK (item_count > 0)
);

CREATE TABLE ai_work_items (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES ai_work_batches(id),
    observation_id TEXT NOT NULL REFERENCES source_observations(id),
    input_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('prepared', 'validated', 'rejected', 'imported')),
    UNIQUE (batch_id, observation_id)
);

CREATE TABLE canonical_jobs (
    id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES source_observations(id),
    input_hash TEXT NOT NULL,
    source_name TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT,
    employer TEXT,
    locations_json TEXT NOT NULL,
    remote_policy TEXT,
    employment_type TEXT,
    seniority TEXT,
    description_summary TEXT,
    requirements_json TEXT NOT NULL,
    application_url TEXT,
    publication_date TEXT,
    application_deadline TEXT,
    language TEXT,
    keywords_json TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    uncertainty_notes_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    prompt_id TEXT NOT NULL,
    prompt_version INTEGER NOT NULL,
    model_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (source_name, external_id)
);

CREATE INDEX idx_ai_work_items_batch ON ai_work_items(batch_id);
CREATE INDEX idx_canonical_jobs_source ON canonical_jobs(source_name, external_id);
CREATE INDEX idx_canonical_jobs_title ON canonical_jobs(title);
