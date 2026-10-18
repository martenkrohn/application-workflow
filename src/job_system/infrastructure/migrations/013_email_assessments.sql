CREATE TABLE email_assessment_batches (
    id TEXT PRIMARY KEY,
    prompt_id TEXT NOT NULL,
    prompt_version INTEGER NOT NULL,
    manifest_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('prepared', 'partial', 'validated', 'imported')),
    input_directory TEXT NOT NULL,
    output_path TEXT NOT NULL,
    item_count INTEGER NOT NULL CHECK (item_count > 0)
);

CREATE TABLE email_assessment_items (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES email_assessment_batches(id) ON DELETE CASCADE,
    gmail_message_id TEXT NOT NULL REFERENCES application_emails(gmail_message_id),
    input_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('prepared', 'validated', 'imported')),
    UNIQUE (batch_id, gmail_message_id)
);

CREATE TABLE email_assessments (
    gmail_message_id TEXT NOT NULL REFERENCES application_emails(gmail_message_id),
    prompt_id TEXT NOT NULL,
    prompt_version INTEGER NOT NULL,
    input_hash TEXT NOT NULL,
    deterministic_application_id TEXT REFERENCES application_workspaces(application_id),
    suggested_application_id TEXT REFERENCES application_workspaces(application_id),
    classification TEXT NOT NULL CHECK (classification IN (
        'invitation', 'rejection', 'action_required', 'acknowledgement',
        'informational', 'unrelated', 'ambiguous'
    )),
    confidence INTEGER NOT NULL CHECK (confidence BETWEEN 0 AND 100),
    reason TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    proposed_status TEXT CHECK (proposed_status IN ('invited', 'rejected')),
    suspicious_content INTEGER NOT NULL CHECK (suspicious_content IN (0, 1)),
    model_id TEXT NOT NULL,
    result_json TEXT NOT NULL,
    assessed_at TEXT NOT NULL,
    applied_transition_at TEXT,
    PRIMARY KEY (gmail_message_id, prompt_id, prompt_version)
);

CREATE INDEX idx_email_assessments_application
    ON email_assessments(suggested_application_id, assessed_at DESC);
