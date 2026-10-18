CREATE TABLE assessment_queue_drafts (
    profile_id TEXT PRIMARY KEY REFERENCES profile_versions(id),
    draft_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
