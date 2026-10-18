CREATE TABLE ai_work_batches_v2 (
    id TEXT PRIMARY KEY,
    prompt_id TEXT NOT NULL,
    prompt_version INTEGER NOT NULL,
    manifest_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('prepared', 'validated', 'partial', 'imported', 'cancelled')
    ),
    input_directory TEXT NOT NULL,
    output_path TEXT NOT NULL,
    item_count INTEGER NOT NULL CHECK (item_count > 0),
    cancelled_at TEXT,
    cancellation_reason TEXT
);

INSERT INTO ai_work_batches_v2 (
    id, prompt_id, prompt_version, manifest_hash, created_at, status,
    input_directory, output_path, item_count, cancelled_at, cancellation_reason
)
SELECT id, prompt_id, prompt_version, manifest_hash, created_at, status,
       input_directory, output_path, item_count, NULL, NULL
FROM ai_work_batches;

CREATE TABLE ai_work_items_v2 (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES ai_work_batches_v2(id),
    observation_id TEXT NOT NULL REFERENCES source_observations(id),
    input_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('prepared', 'validated', 'rejected', 'imported', 'cancelled')
    ),
    UNIQUE (batch_id, observation_id)
);

INSERT INTO ai_work_items_v2 (id, batch_id, observation_id, input_hash, status)
SELECT id, batch_id, observation_id, input_hash, status
FROM ai_work_items;

DROP TABLE ai_work_items;
DROP TABLE ai_work_batches;
ALTER TABLE ai_work_batches_v2 RENAME TO ai_work_batches;
ALTER TABLE ai_work_items_v2 RENAME TO ai_work_items;

CREATE INDEX idx_ai_work_items_batch ON ai_work_items(batch_id);
