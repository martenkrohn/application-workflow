CREATE TABLE observation_artifacts (
    observation_id TEXT NOT NULL REFERENCES source_observations(id),
    artifact_hash TEXT NOT NULL REFERENCES raw_artifacts(content_hash),
    role TEXT NOT NULL,
    PRIMARY KEY (observation_id, artifact_hash, role)
);

INSERT INTO observation_artifacts (observation_id, artifact_hash, role)
SELECT id, artifact_hash, 'observation' FROM source_observations;

CREATE INDEX idx_observation_artifacts_hash
ON observation_artifacts(artifact_hash);
