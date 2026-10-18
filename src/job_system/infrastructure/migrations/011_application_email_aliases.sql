ALTER TABLE vacancy_review_decisions RENAME TO vacancy_review_decisions_legacy;

CREATE TABLE vacancy_review_decisions (
    group_id TEXT NOT NULL REFERENCES vacancy_groups(id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL REFERENCES profile_versions(id),
    status TEXT NOT NULL DEFAULT 'new'
        CHECK (status IN (
            'new', 'interested', 'deferred', 'rejected', 'applied', 'invited', 'abandoned'
        )),
    note TEXT,
    decided_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (group_id, profile_id)
);

INSERT INTO vacancy_review_decisions (
    group_id, profile_id, status, note, decided_at, updated_at
)
SELECT group_id, profile_id, status, note, decided_at, updated_at
FROM vacancy_review_decisions_legacy;

DROP TABLE vacancy_review_decisions_legacy;

CREATE INDEX idx_vacancy_review_decisions_profile_status
    ON vacancy_review_decisions(profile_id, status, updated_at DESC);

ALTER TABLE application_workspaces RENAME TO application_workspaces_legacy;

CREATE TABLE application_workspaces (
    group_id TEXT NOT NULL REFERENCES vacancy_groups(id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL REFERENCES profile_versions(id),
    relative_path TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL,
    application_id TEXT NOT NULL UNIQUE,
    contact_email TEXT UNIQUE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (group_id, profile_id)
);

INSERT INTO application_workspaces (
    group_id, profile_id, relative_path, slug, application_id, contact_email, created_at
)
SELECT group_id, profile_id, relative_path, slug,
       substr(relative_path, instr(relative_path, '/') + 1, 2), NULL, created_at
FROM application_workspaces_legacy;

DROP TABLE application_workspaces_legacy;

CREATE INDEX idx_application_workspaces_profile
    ON application_workspaces(profile_id, created_at DESC);
