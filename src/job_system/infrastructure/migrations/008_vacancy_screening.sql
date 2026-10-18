ALTER TABLE ai_work_batches ADD COLUMN cycle_id TEXT;

CREATE TABLE vacancy_screenings (
    id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES source_observations(id),
    source_name TEXT NOT NULL,
    external_id TEXT NOT NULL,
    artifact_hash TEXT NOT NULL,
    profile_id TEXT NOT NULL REFERENCES profile_versions(id),
    screening_cycle_id TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    suitability_score INTEGER NOT NULL CHECK (suitability_score BETWEEN 0 AND 100),
    assessment_confidence INTEGER NOT NULL CHECK (assessment_confidence BETWEEN 0 AND 100),
    decision TEXT NOT NULL CHECK (decision IN ('promote', 'deprioritize')),
    summary TEXT NOT NULL,
    result_json TEXT NOT NULL,
    prompt_id TEXT NOT NULL,
    prompt_version INTEGER NOT NULL,
    model_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (source_name, external_id, profile_id)
);

CREATE INDEX idx_vacancy_screenings_rank
    ON vacancy_screenings(profile_id, suitability_score DESC, assessment_confidence DESC);
