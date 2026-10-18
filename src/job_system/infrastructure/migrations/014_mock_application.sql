ALTER TABLE application_workspaces ADD COLUMN is_mock INTEGER NOT NULL DEFAULT 0
    CHECK (is_mock IN (0, 1));

CREATE UNIQUE INDEX idx_application_workspaces_single_mock
    ON application_workspaces(is_mock) WHERE is_mock = 1;
