BEGIN;

-- ============================================================
-- MODE DE CRÉATION DU PROJET
-- ============================================================

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS operation_mode VARCHAR(30)
        NOT NULL DEFAULT 'new_application',

    ADD COLUMN IF NOT EXISTS source_type VARCHAR(20)
        NOT NULL DEFAULT 'git',

    ADD COLUMN IF NOT EXISTS archive_original_name VARCHAR(255),

    ADD COLUMN IF NOT EXISTS archive_stored_name VARCHAR(255),

    ADD COLUMN IF NOT EXISTS archive_storage_path TEXT,

    ADD COLUMN IF NOT EXISTS archive_size_bytes BIGINT,

    ADD COLUMN IF NOT EXISTS archive_sha256 VARCHAR(64),

    ADD COLUMN IF NOT EXISTS archive_entry_count INTEGER,

    ADD COLUMN IF NOT EXISTS archive_uncompressed_bytes BIGINT;


UPDATE projects
SET operation_mode = 'new_application'
WHERE operation_mode IS NULL
   OR operation_mode NOT IN (
       'new_application',
       'adopt_existing'
   );


UPDATE projects
SET source_type = 'git'
WHERE source_type IS NULL
   OR source_type NOT IN (
       'git',
       'zip'
   );


-- Les colonnes Git deviennent facultatives pour une source ZIP.
ALTER TABLE projects
    ALTER COLUMN default_branch DROP NOT NULL,
    ALTER COLUMN repository_visibility DROP NOT NULL,
    ALTER COLUMN source_transport DROP NOT NULL,
    ALTER COLUMN source_credential_source DROP NOT NULL,
    ALTER COLUMN source_auth_method DROP NOT NULL;


-- La colonne historique source_provider reste utilisée.
ALTER TABLE projects
    DROP CONSTRAINT IF EXISTS projects_source_provider_check;

ALTER TABLE projects
    ADD CONSTRAINT projects_source_provider_check
    CHECK (
        source_provider IN (
            'gitlab',
            'archive'
        )
    );


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'projects_operation_mode_check'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT projects_operation_mode_check
            CHECK (
                operation_mode IN (
                    'new_application',
                    'adopt_existing'
                )
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'projects_source_type_check'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT projects_source_type_check
            CHECK (
                source_type IN (
                    'git',
                    'zip'
                )
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'projects_archive_size_check'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT projects_archive_size_check
            CHECK (
                archive_size_bytes IS NULL
                OR archive_size_bytes >= 0
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'projects_archive_entry_count_check'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT projects_archive_entry_count_check
            CHECK (
                archive_entry_count IS NULL
                OR archive_entry_count >= 0
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'projects_archive_uncompressed_check'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT projects_archive_uncompressed_check
            CHECK (
                archive_uncompressed_bytes IS NULL
                OR archive_uncompressed_bytes >= 0
            );
    END IF;
END;
$$;


CREATE INDEX IF NOT EXISTS projects_operation_mode_idx
ON projects (
    operation_mode
);


CREATE INDEX IF NOT EXISTS projects_source_type_idx
ON projects (
    source_type
);


CREATE UNIQUE INDEX IF NOT EXISTS projects_archive_stored_name_unique_idx
ON projects (
    archive_stored_name
)
WHERE archive_stored_name IS NOT NULL;


INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    '012',
    'Modes projet, source ZIP et environnement unique à la création'
)
ON CONFLICT (version)
DO NOTHING;


COMMIT;