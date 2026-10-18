CREATE TABLE clean_vacancy_versions (
    id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    external_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    extractor_version TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    document_hash TEXT NOT NULL,
    quality_state TEXT NOT NULL CHECK (quality_state IN ('ready','needs_review')),
    document_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(source_name,external_id,schema_version,extractor_version,content_hash)
);
CREATE TABLE clean_observation_versions (
    observation_id TEXT NOT NULL REFERENCES source_observations(id),
    extractor_version TEXT NOT NULL,
    version_id TEXT NOT NULL REFERENCES clean_vacancy_versions(id),
    provenance_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(observation_id,extractor_version)
);
CREATE TABLE clean_vacancy_current (
    source_name TEXT NOT NULL,
    external_id TEXT NOT NULL,
    version_id TEXT NOT NULL REFERENCES clean_vacancy_versions(id),
    last_ready_version_id TEXT REFERENCES clean_vacancy_versions(id),
    observation_id TEXT NOT NULL REFERENCES source_observations(id),
    PRIMARY KEY(source_name,external_id)
);
CREATE TABLE clean_assessment_items (
    work_item_id TEXT PRIMARY KEY REFERENCES ai_work_items(id),
    version_id TEXT NOT NULL REFERENCES clean_vacancy_versions(id)
);
CREATE TABLE clean_assessment_history (
    work_item_id TEXT PRIMARY KEY REFERENCES ai_work_items(id),
    version_id TEXT NOT NULL REFERENCES clean_vacancy_versions(id),
    profile_id TEXT NOT NULL REFERENCES profile_versions(id),
    prompt_version INTEGER NOT NULL,
    input_hash TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX clean_assessment_reuse ON clean_assessment_history(
    version_id, profile_id, prompt_version
);
