BEGIN;

-- ============================================================
-- CONFIGURATION DU SERVEUR GITLAB
-- ============================================================

ALTER TABLE integration_connections
    ADD COLUMN IF NOT EXISTS git_transport VARCHAR(20)
        NOT NULL DEFAULT 'https',

    ADD COLUMN IF NOT EXISTS ssh_host VARCHAR(255),

    ADD COLUMN IF NOT EXISTS ssh_port INTEGER
        NOT NULL DEFAULT 22,

    ADD COLUMN IF NOT EXISTS ssh_username VARCHAR(100)
        NOT NULL DEFAULT 'git';


-- ============================================================
-- PROFILS D'ACCÈS GIT
--
-- Une connexion représente le serveur GitLab.
-- Un profil représente un token ou une clé SSH.
-- ============================================================

CREATE TABLE IF NOT EXISTS git_access_profiles (
    id BIGSERIAL PRIMARY KEY,

    connection_id BIGINT NOT NULL,

    name VARCHAR(140) NOT NULL,

    transport VARCHAR(20) NOT NULL,

    credential_type VARCHAR(40) NOT NULL,

    username VARCHAR(255),

    secret_ciphertext TEXT NOT NULL,

    enabled BOOLEAN NOT NULL DEFAULT TRUE,

    description TEXT,

    created_by BIGINT,

    created_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT git_access_profiles_connection_fk
        FOREIGN KEY (connection_id)
        REFERENCES integration_connections (id)
        ON DELETE CASCADE,

    CONSTRAINT git_access_profiles_created_by_fk
        FOREIGN KEY (created_by)
        REFERENCES users (id)
        ON DELETE SET NULL,

    CONSTRAINT git_access_profiles_transport_check
        CHECK (
            transport IN (
                'https',
                'ssh'
            )
        ),

    CONSTRAINT git_access_profiles_credential_type_check
        CHECK (
            credential_type IN (
                'generic_token',
                'personal_access_token',
                'project_access_token',
                'group_access_token',
                'deploy_token',
                'ssh_deploy_key'
            )
        )
);


CREATE UNIQUE INDEX IF NOT EXISTS
    git_access_profiles_connection_name_unique_idx
ON git_access_profiles (
    connection_id,
    LOWER(name)
);


CREATE INDEX IF NOT EXISTS
    git_access_profiles_connection_idx
ON git_access_profiles (
    connection_id
);


-- ============================================================
-- MIGRATION DU CREDENTIAL GITLAB EXISTANT
--
-- Votre connexion GitLab actuelle possède déjà un credential.
-- Il devient automatiquement un premier profil d'accès.
-- ============================================================

INSERT INTO git_access_profiles (
    connection_id,
    name,
    transport,
    credential_type,
    username,
    secret_ciphertext,
    description
)
SELECT
    integration.id,

    integration.name || ' - Profil par défaut',

    CASE
        WHEN credential.auth_type = 'ssh_key'
            THEN 'ssh'
        ELSE 'https'
    END,

    CASE
        WHEN credential.auth_type = 'basic'
            THEN 'deploy_token'

        WHEN credential.auth_type = 'ssh_key'
            THEN 'ssh_deploy_key'

        ELSE 'generic_token'
    END,

    credential.username,

    credential.secret_ciphertext,

    'Profil migré depuis la connexion GitLab existante'

FROM integration_connections AS integration

INNER JOIN integration_credentials AS credential
    ON credential.connection_id = integration.id

WHERE
    integration.provider_type = 'gitlab'

    AND credential.secret_ciphertext
        IS NOT NULL

    AND credential.auth_type IN (
        'token',
        'basic',
        'ssh_key'
    )

    AND NOT EXISTS (
        SELECT 1

        FROM git_access_profiles AS profile

        WHERE
            profile.connection_id = integration.id
            AND profile.name =
                integration.name || ' - Profil par défaut'
    );


-- ============================================================
-- NOUVELLE CONFIGURATION DES PROJETS
-- ============================================================

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS source_access_profile_id BIGINT,

    ADD COLUMN IF NOT EXISTS repository_visibility VARCHAR(20)
        NOT NULL DEFAULT 'private',

    ADD COLUMN IF NOT EXISTS source_transport VARCHAR(20)
        NOT NULL DEFAULT 'https',

    ADD COLUMN IF NOT EXISTS repository_url TEXT,

    ADD COLUMN IF NOT EXISTS repository_path TEXT,

    ADD COLUMN IF NOT EXISTS default_branch VARCHAR(255)
        NOT NULL DEFAULT 'main',

    ADD COLUMN IF NOT EXISTS source_subdirectory TEXT,

    ADD COLUMN IF NOT EXISTS source_status VARCHAR(30)
        NOT NULL DEFAULT 'unchecked',

    ADD COLUMN IF NOT EXISTS source_error TEXT,

    ADD COLUMN IF NOT EXISTS last_source_commit_sha VARCHAR(100),

    ADD COLUMN IF NOT EXISTS last_source_check_at TIMESTAMPTZ,

    ADD COLUMN IF NOT EXISTS default_environment_id BIGINT;


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname =
            'projects_source_access_profile_fk'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT
                projects_source_access_profile_fk
            FOREIGN KEY (
                source_access_profile_id
            )
            REFERENCES git_access_profiles (id)
            ON DELETE SET NULL;
    END IF;


    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname =
            'projects_source_connection_fk'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT
                projects_source_connection_fk
            FOREIGN KEY (
                source_connection_id
            )
            REFERENCES integration_connections (id)
            ON DELETE SET NULL;
    END IF;


    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname =
            'projects_default_environment_fk'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT
                projects_default_environment_fk
            FOREIGN KEY (
                default_environment_id
            )
            REFERENCES deployment_environments (id)
            ON DELETE SET NULL;
    END IF;
END;
$$;


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname =
            'projects_repository_visibility_check'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT
                projects_repository_visibility_check
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


-- ============================================================
-- ASSOCIATION PROJET / ENVIRONNEMENT
-- ============================================================

CREATE TABLE IF NOT EXISTS project_environments (
    project_id BIGINT NOT NULL,

    environment_id BIGINT NOT NULL,

    is_default BOOLEAN NOT NULL DEFAULT FALSE,

    created_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (
        project_id,
        environment_id
    ),

    CONSTRAINT project_environments_project_fk
        FOREIGN KEY (project_id)
        REFERENCES projects (id)
        ON DELETE CASCADE,

    CONSTRAINT project_environments_environment_fk
        FOREIGN KEY (environment_id)
        REFERENCES deployment_environments (id)
        ON DELETE CASCADE
);


-- ============================================================
-- HISTORIQUE DES TESTS DE REPOSITORY
-- ============================================================

CREATE TABLE IF NOT EXISTS project_source_checks (
    id BIGSERIAL PRIMARY KEY,

    project_id BIGINT,

    user_id BIGINT,

    source_connection_id BIGINT,

    repository_path TEXT NOT NULL,

    branch VARCHAR(255) NOT NULL,

    status VARCHAR(30) NOT NULL,

    commit_sha VARCHAR(100),

    error_code VARCHAR(100),

    error_message TEXT,

    details JSONB NOT NULL DEFAULT '{}'::JSONB,

    checked_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT project_source_checks_project_fk
        FOREIGN KEY (project_id)
        REFERENCES projects (id)
        ON DELETE SET NULL,

    CONSTRAINT project_source_checks_user_fk
        FOREIGN KEY (user_id)
        REFERENCES users (id)
        ON DELETE SET NULL,

    CONSTRAINT project_source_checks_connection_fk
        FOREIGN KEY (source_connection_id)
        REFERENCES integration_connections (id)
        ON DELETE SET NULL,

    CONSTRAINT project_source_checks_status_check
        CHECK (
            status IN (
                'valid',
                'invalid',
                'error'
            )
        )
);


CREATE INDEX IF NOT EXISTS
    project_source_checks_project_idx
ON project_source_checks (
    project_id,
    checked_at DESC
);


-- ============================================================
-- ACTIVITÉ DU PROJET
-- ============================================================

CREATE TABLE IF NOT EXISTS project_activity_logs (
    id BIGSERIAL PRIMARY KEY,

    project_id BIGINT NOT NULL,

    user_id BIGINT,

    action VARCHAR(100) NOT NULL,

    details JSONB NOT NULL DEFAULT '{}'::JSONB,

    created_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT project_activity_logs_project_fk
        FOREIGN KEY (project_id)
        REFERENCES projects (id)
        ON DELETE CASCADE,

    CONSTRAINT project_activity_logs_user_fk
        FOREIGN KEY (user_id)
        REFERENCES users (id)
        ON DELETE SET NULL
);


INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    '009',
    'Projets sans client et profils accès Git'
)
ON CONFLICT (version)
DO NOTHING;


COMMIT;