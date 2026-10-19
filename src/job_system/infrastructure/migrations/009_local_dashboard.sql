ALTER TABLE vacancy_review_decisions RENAME TO vacancy_review_decisions_legacy;

CREATE TABLE vacancy_review_decisions (
    group_id TEXT NOT NULL REFERENCES vacancy_groups(id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL REFERENCES profile_versions(id),
    status TEXT NOT NULL DEFAULT 'new'
        CHECK (status IN ('new', 'interested', 'deferred', 'rejected', 'applied')),
    note TEXT,
    decided_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (group_id, profile_id)
);

INSERT INTO vacancy_review_decisions (
    group_id, profile_id, status, note, decided_at, updated_at
)
SELECT group_id, profile_id,
       CASE status WHEN 'maybe' THEN 'deferred' WHEN 'dismissed' THEN 'rejected' ELSE status END,
       note, decided_at, updated_at
FROM vacancy_review_decisions_legacy;

DROP TABLE vacancy_review_decisions_legacy;

CREATE INDEX idx_vacancy_review_decisions_profile_status
    ON vacancy_review_decisions(profile_id, status, updated_at DESC);
