BEGIN;

-- ============================================================
-- CONFIGURATION GIT DES CONNEXIONS
-- ============================================================

ALTER TABLE integration_connections
    ADD COLUMN IF NOT EXISTS git_transport VARCHAR(20)
        NOT NULL DEFAULT 'https',

    ADD COLUMN IF NOT EXISTS ssh_host VARCHAR(255),

    ADD COLUMN IF NOT EXISTS ssh_port INTEGER
        NOT NULL DEFAULT 22,

    ADD COLUMN IF NOT EXISTS ssh_username VARCHAR(100)
        NOT NULL DEFAULT 'git';


UPDATE integration_connections
SET git_transport = 'https'
WHERE git_transport IS NULL;


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname =
            'integration_connections_git_transport_check'
    ) THEN
        ALTER TABLE integration_connections
            ADD CONSTRAINT
                integration_connections_git_transport_check
            CHECK (
                git_transport IN (
                    'https',
                    'ssh'
                )
            );
    END IF;
END;
$$;


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname =
            'integration_connections_ssh_port_check'
    ) THEN
        ALTER TABLE integration_connections
            ADD CONSTRAINT
                integration_connections_ssh_port_check
            CHECK (
                ssh_port BETWEEN 1 AND 65535
            );
    END IF;
END;
$$;


-- ============================================================
-- AJOUT DU TYPE DE CREDENTIAL SSH
-- ============================================================

DO $$
DECLARE
    constraint_record RECORD;
BEGIN
    FOR constraint_record IN
        SELECT conname
        FROM pg_constraint
        WHERE
            conrelid =
                'integration_credentials'::REGCLASS

            AND contype = 'c'

            AND pg_get_constraintdef(oid)
                ILIKE '%auth_type%'
    LOOP
        EXECUTE FORMAT(
            'ALTER TABLE integration_credentials
             DROP CONSTRAINT %I',
            constraint_record.conname
        );
    END LOOP;
END;
$$;


ALTER TABLE integration_credentials
    ADD CONSTRAINT
        integration_credentials_auth_type_check
    CHECK (
        auth_type IN (
            'none',
            'token',
            'basic',
            'ssh_key'
        )
    );


-- ============================================================
-- CONFIGURATION DE LA SOURCE DANS LE PROJET
-- ============================================================

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS source_access_mode VARCHAR(30)
        NOT NULL DEFAULT 'private_https',

    ADD COLUMN IF NOT EXISTS source_transport VARCHAR(20)
        NOT NULL DEFAULT 'https',

    ADD COLUMN IF NOT EXISTS source_server_url TEXT,

    ADD COLUMN IF NOT EXISTS repository_clone_url TEXT,

    ADD COLUMN IF NOT EXISTS clone_strategy VARCHAR(30)
        NOT NULL DEFAULT 'shallow';


UPDATE projects
SET
    source_access_mode = 'private_https',
    source_transport = 'https',
    clone_strategy = 'shallow'
WHERE source_access_mode IS NULL;


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname =
            'projects_source_access_mode_check'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT
                projects_source_access_mode_check
            CHECK (
                source_access_mode IN (
                    'public_https',
                    'private_https',
                    'private_ssh'
                )
            );
    END IF;
END;
$$;


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname =
            'projects_source_transport_check'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT
                projects_source_transport_check
            CHECK (
                source_transport IN (
                    'https',
                    'ssh'
                )
            );
    END IF;
END;
$$;


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname =
            'projects_clone_strategy_check'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT
                projects_clone_strategy_check
            CHECK (
                clone_strategy IN (
                    'shallow',
                    'full',
                    'sparse'
                )
            );
    END IF;
END;
$$;


INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    '008',
    'Modes accès Git HTTPS SSH et stratégies de clonage'
)
ON CONFLICT (version)
DO NOTHING;


COMMIT;