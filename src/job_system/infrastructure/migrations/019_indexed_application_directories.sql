-- Folder names change; lifecycle statuses and application identities stay stable.
UPDATE application_workspaces
SET relative_path = CASE
    WHEN relative_path LIKE 'applications/active/%'
        THEN 'applications/01_active/' || substr(relative_path, length('applications/active/') + 1)
    WHEN relative_path LIKE 'applications/applied/%'
        THEN 'applications/02_applied/' || substr(relative_path, length('applications/applied/') + 1)
    WHEN relative_path LIKE 'applications/invited/%'
        THEN 'applications/03_invited/' || substr(relative_path, length('applications/invited/') + 1)
    WHEN relative_path LIKE 'applications/rejected/%'
        THEN 'applications/04_rejected/' || substr(relative_path, length('applications/rejected/') + 1)
    WHEN relative_path LIKE 'applications/abandoned/%'
        THEN 'applications/05_abandoned/' || substr(relative_path, length('applications/abandoned/') + 1)
    ELSE relative_path
END
WHERE relative_path LIKE 'applications/active/%'
   OR relative_path LIKE 'applications/applied/%'
   OR relative_path LIKE 'applications/invited/%'
   OR relative_path LIKE 'applications/rejected/%'
   OR relative_path LIKE 'applications/abandoned/%';
