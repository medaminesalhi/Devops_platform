BEGIN;

-- Le concept client n'est plus utilisé par le module Projet.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE
            table_name = 'projects'
            AND column_name = 'client_id'
    ) THEN
        ALTER TABLE projects
            ALTER COLUMN client_id DROP NOT NULL;
    END IF;
END;
$$;


-- Informations Git nécessaires au serveur.
ALTER TABLE integration_connections
    ADD COLUMN IF NOT EXISTS ssh_host VARCHAR(255),

    ADD COLUMN IF NOT EXISTS ssh_port INTEGER
        NOT NULL DEFAULT 22,

    ADD COLUMN IF NOT EXISTS ssh_username VARCHAR(100)
        NOT NULL DEFAULT 'git';


-- Nouvelle configuration de la source du projet.
ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS repository_url TEXT,

    ADD COLUMN IF NOT EXISTS repository_path TEXT,

    ADD COLUMN IF NOT EXISTS repository_visibility VARCHAR(20)
        NOT NULL DEFAULT 'private',

    ADD COLUMN IF NOT EXISTS source_transport VARCHAR(20)
        NOT NULL DEFAULT 'https',

    ADD COLUMN IF NOT EXISTS source_credential_source VARCHAR(20)
        NOT NULL DEFAULT 'project',

    ADD COLUMN IF NOT EXISTS source_auth_method VARCHAR(30)
        NOT NULL DEFAULT 'https_token',

    ADD COLUMN IF NOT EXISTS source_token_type VARCHAR(40),

    ADD COLUMN IF NOT EXISTS source_username VARCHAR(255),

    ADD COLUMN IF NOT EXISTS default_branch VARCHAR(255)
        NOT NULL DEFAULT 'main',

    ADD COLUMN IF NOT EXISTS source_subdirectory TEXT,

    ADD COLUMN IF NOT EXISTS source_status VARCHAR(30)
        NOT NULL DEFAULT 'unchecked',

    ADD COLUMN IF NOT EXISTS source_error TEXT,

    ADD COLUMN IF NOT EXISTS last_source_commit_sha VARCHAR(100),

    ADD COLUMN IF NOT EXISTS last_source_check_at TIMESTAMPTZ;


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'projects_repository_visibility_check'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT projects_repository_visibility_check
            CHECK (
                repository_visibility IN (
                    'public',
                    'private'
                )
            );
    END IF;


    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'projects_source_transport_check'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT projects_source_transport_check
            CHECK (
                source_transport IN (
                    'https',
                    'ssh'
                )
            );
    END IF;


    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'projects_source_credential_source_check'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT projects_source_credential_source_check
            CHECK (
                source_credential_source IN (
                    'none',
                    'integration',
                    'project'
                )
            );
    END IF;


    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'projects_source_auth_method_check'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT projects_source_auth_method_check
            CHECK (
                source_auth_method IN (
                    'none',
                    'https_password',
                    'https_token',
                    'ssh_key'
                )
            );
    END IF;
END;
$$;


-- Secret propre à un projet.
CREATE TABLE IF NOT EXISTS project_source_credentials (
    id BIGSERIAL PRIMARY KEY,

    project_id BIGINT NOT NULL UNIQUE,

    secret_ciphertext TEXT NOT NULL,

    created_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT project_source_credentials_project_fk
        FOREIGN KEY (project_id)
        REFERENCES projects (id)
        ON DELETE CASCADE
);


CREATE INDEX IF NOT EXISTS
    project_source_credentials_project_idx
ON project_source_credentials (
    project_id
);


INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    '010',
    'Credentials Git provenant de l integration ou du projet'
)
ON CONFLICT (version)
DO NOTHING;


COMMIT;