CREATE TABLE vacancy_groups (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE vacancy_group_members (
    group_id TEXT NOT NULL REFERENCES vacancy_groups(id) ON DELETE CASCADE,
    source_name TEXT NOT NULL,
    external_id TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (source_name, external_id),
    UNIQUE (group_id, source_name, external_id)
);

CREATE INDEX idx_vacancy_group_members_group
    ON vacancy_group_members(group_id);

CREATE TABLE vacancy_review_decisions (
    group_id TEXT NOT NULL REFERENCES vacancy_groups(id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL REFERENCES profile_versions(id),
    status TEXT NOT NULL DEFAULT 'new'
        CHECK (status IN ('new', 'interested', 'maybe', 'dismissed', 'applied')),
    note TEXT,
    decided_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (group_id, profile_id)
);

CREATE INDEX idx_vacancy_review_decisions_profile_status
    ON vacancy_review_decisions(profile_id, status, updated_at DESC);
