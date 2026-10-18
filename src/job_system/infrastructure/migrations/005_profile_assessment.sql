ALTER TABLE canonical_jobs ADD COLUMN working_time TEXT;
ALTER TABLE canonical_jobs ADD COLUMN contract_type TEXT;
ALTER TABLE canonical_jobs ADD COLUMN contract_end_date TEXT;
ALTER TABLE canonical_jobs ADD COLUMN start_date TEXT;
ALTER TABLE canonical_jobs ADD COLUMN salary TEXT;
ALTER TABLE canonical_jobs ADD COLUMN responsibilities_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE canonical_jobs ADD COLUMN mandatory_requirements_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE canonical_jobs ADD COLUMN preferred_qualifications_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE canonical_jobs ADD COLUMN education_requirements_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE canonical_jobs ADD COLUMN experience_requirements_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE canonical_jobs ADD COLUMN methods_tools_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE canonical_jobs ADD COLUMN languages_required_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE canonical_jobs ADD COLUMN occupational_categories_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE canonical_jobs ADD COLUMN application_method TEXT;
ALTER TABLE canonical_jobs ADD COLUMN contact_details_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE canonical_jobs ADD COLUMN reference_number TEXT;
ALTER TABLE canonical_jobs ADD COLUMN validity_status TEXT;

CREATE TABLE profile_versions (
    id TEXT PRIMARY KEY,
    profile_name TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    content_hash TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (profile_name, version)
);

CREATE TABLE job_evaluations (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES canonical_jobs(id),
    observation_id TEXT NOT NULL REFERENCES source_observations(id),
    source_name TEXT NOT NULL,
    external_id TEXT NOT NULL,
    artifact_hash TEXT NOT NULL,
    profile_id TEXT NOT NULL REFERENCES profile_versions(id),
    input_hash TEXT NOT NULL,
    suitability_score INTEGER NOT NULL CHECK (suitability_score BETWEEN 0 AND 100),
    assessment_confidence INTEGER NOT NULL CHECK (assessment_confidence BETWEEN 0 AND 100),
    recommendation TEXT NOT NULL CHECK (recommendation IN ('high', 'medium', 'low', 'reject')),
    summary TEXT NOT NULL,
    strengths_json TEXT NOT NULL,
    gaps_json TEXT NOT NULL,
    deal_breakers_json TEXT NOT NULL,
    missing_information_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    prompt_id TEXT NOT NULL,
    prompt_version INTEGER NOT NULL,
    model_id TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'new'
        CHECK (review_status IN ('new', 'reviewed', 'dismissed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (source_name, external_id, profile_id)
);

CREATE INDEX idx_job_evaluations_rank
    ON job_evaluations(profile_id, review_status, suitability_score DESC);
