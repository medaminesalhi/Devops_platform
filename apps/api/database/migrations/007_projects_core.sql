BEGIN;

-- ============================================================
-- PROJETS
--
-- La table projects existait déjà.
-- Cette migration ajoute les informations nécessaires
-- à la gestion complète d'un projet source GitLab.
-- ============================================================

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS client_id BIGINT,

    ADD COLUMN IF NOT EXISTS name VARCHAR(140),

    ADD COLUMN IF NOT EXISTS slug VARCHAR(160),

    ADD COLUMN IF NOT EXISTS description TEXT,

    ADD COLUMN IF NOT EXISTS source_provider VARCHAR(30)
        NOT NULL DEFAULT 'gitlab',

    ADD COLUMN IF NOT EXISTS source_connection_id BIGINT,

    ADD COLUMN IF NOT EXISTS repository_path TEXT,

    ADD COLUMN IF NOT EXISTS repository_url TEXT,

    ADD COLUMN IF NOT EXISTS default_branch VARCHAR(255)
        NOT NULL DEFAULT 'main',

    ADD COLUMN IF NOT EXISTS source_subdirectory TEXT,

    ADD COLUMN IF NOT EXISTS external_project_id BIGINT,

    ADD COLUMN IF NOT EXISTS source_visibility VARCHAR(30),

    ADD COLUMN IF NOT EXISTS source_status VARCHAR(30)
        NOT NULL DEFAULT 'unchecked',

    ADD COLUMN IF NOT EXISTS source_error TEXT,

    ADD COLUMN IF NOT EXISTS source_web_url TEXT,

    ADD COLUMN IF NOT EXISTS last_source_check_at TIMESTAMPTZ,

    ADD COLUMN IF NOT EXISTS last_source_commit_sha VARCHAR(100),

    ADD COLUMN IF NOT EXISTS default_environment_id BIGINT,

    ADD COLUMN IF NOT EXISTS status VARCHAR(30)
        NOT NULL DEFAULT 'draft',

    ADD COLUMN IF NOT EXISTS created_by BIGINT,

    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;


-- ============================================================
-- NETTOYAGE DES ANCIENNES LIGNES ÉVENTUELLES
--
-- Cela permet d'ajouter ensuite les contraintes
-- sans échouer sur une ancienne ligne vide.
-- ============================================================

UPDATE projects
SET name = 'Projet ' || id
WHERE name IS NULL OR BTRIM(name) = '';


UPDATE projects
SET slug = 'project-' || id
WHERE slug IS NULL OR BTRIM(slug) = '';


UPDATE projects
SET source_provider = 'gitlab'
WHERE source_provider IS NULL;


UPDATE projects
SET default_branch = 'main'
WHERE default_branch IS NULL
   OR BTRIM(default_branch) = '';


UPDATE projects
SET source_status = 'unchecked'
WHERE source_status IS NULL
   OR source_status NOT IN (
       'unchecked',
       'valid',
       'invalid',
       'error'
   );


UPDATE projects
SET status = 'draft'
WHERE status IS NULL
   OR status NOT IN (
       'draft',
       'active',
       'source_error',
       'archived'
   );


ALTER TABLE projects
    ALTER COLUMN name SET NOT NULL,

    ALTER COLUMN slug SET NOT NULL,

    ALTER COLUMN source_provider SET NOT NULL,

    ALTER COLUMN default_branch SET NOT NULL,

    ALTER COLUMN source_status SET NOT NULL,

    ALTER COLUMN status SET NOT NULL;


-- ============================================================
-- CLÉS ÉTRANGÈRES
-- ============================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'projects_client_fk'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT projects_client_fk
            FOREIGN KEY (client_id)
            REFERENCES clients (id)
            ON DELETE SET NULL;
    END IF;


    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'projects_source_connection_fk'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT projects_source_connection_fk
            FOREIGN KEY (source_connection_id)
            REFERENCES integration_connections (id)
            ON DELETE SET NULL;
    END IF;


    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'projects_default_environment_fk'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT projects_default_environment_fk
            FOREIGN KEY (default_environment_id)
            REFERENCES deployment_environments (id)
            ON DELETE SET NULL;
    END IF;


    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'projects_created_by_fk'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT projects_created_by_fk
            FOREIGN KEY (created_by)
            REFERENCES users (id)
            ON DELETE SET NULL;
    END IF;
END;
$$;


-- ============================================================
-- CONTRAINTES DE VALEURS
-- ============================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'projects_source_provider_check'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT projects_source_provider_check
            CHECK (
                source_provider IN (
                    'gitlab'
                )
            );
    END IF;


    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'projects_source_status_check'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT projects_source_status_check
            CHECK (
                source_status IN (
                    'unchecked',
                    'valid',
                    'invalid',
                    'error'
                )
            );
    END IF;


    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'projects_status_check'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT projects_status_check
            CHECK (
                status IN (
                    'draft',
                    'active',
                    'source_error',
                    'archived'
                )
            );
    END IF;
END;
$$;


-- ============================================================
-- INDEX DES PROJETS
--
-- Deux projets d'un même client ne peuvent pas avoir
-- le même slug tant qu'ils ne sont pas archivés.
-- ============================================================

CREATE UNIQUE INDEX IF NOT EXISTS
    projects_client_slug_unique_idx
ON projects (
    client_id,
    slug
)
WHERE archived_at IS NULL;


CREATE INDEX IF NOT EXISTS
    projects_client_idx
ON projects (
    client_id
);


CREATE INDEX IF NOT EXISTS
    projects_source_connection_idx
ON projects (
    source_connection_id
);


CREATE INDEX IF NOT EXISTS
    projects_default_environment_idx
ON projects (
    default_environment_id
);


CREATE INDEX IF NOT EXISTS
    projects_status_idx
ON projects (
    status
);


-- ============================================================
-- CONTRÔLES DE SOURCE
--
-- Cette table conserve chaque vérification GitLab :
-- repository trouvé, branche trouvée, erreur 401, etc.
--
-- project_id peut être NULL lorsque l'utilisateur teste
-- la source avant la création du projet.
-- ============================================================

CREATE TABLE IF NOT EXISTS project_source_checks (
    id BIGSERIAL PRIMARY KEY,

    project_id BIGINT,

    user_id BIGINT,

    source_connection_id BIGINT,

    repository_path TEXT NOT NULL,

    branch VARCHAR(255) NOT NULL,

    status VARCHAR(30) NOT NULL,

    external_project_id BIGINT,

    resolved_repository_path TEXT,

    web_url TEXT,

    default_branch VARCHAR(255),

    visibility VARCHAR(30),

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


CREATE INDEX IF NOT EXISTS
    project_source_checks_connection_idx
ON project_source_checks (
    source_connection_id,
    checked_at DESC
);


-- ============================================================
-- ACTIVITÉ DES PROJETS
--
-- Cette table servira à l'onglet Activité :
-- création, analyse, modification, déploiement, etc.
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


CREATE INDEX IF NOT EXISTS
    project_activity_logs_project_idx
ON project_activity_logs (
    project_id,
    created_at DESC
);


-- ============================================================
-- INDEX DE project_environments
-- ============================================================

CREATE INDEX IF NOT EXISTS
    project_environments_project_idx
ON project_environments (
    project_id
);


CREATE INDEX IF NOT EXISTS
    project_environments_environment_idx
ON project_environments (
    environment_id
);


-- ============================================================
-- MISE À JOUR AUTOMATIQUE DE updated_at
-- ============================================================

CREATE OR REPLACE FUNCTION
    set_project_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


DROP TRIGGER IF EXISTS
    projects_updated_at_trigger
ON projects;


CREATE TRIGGER
    projects_updated_at_trigger
BEFORE UPDATE ON projects
FOR EACH ROW
EXECUTE FUNCTION
    set_project_updated_at();


-- ============================================================
-- ENREGISTREMENT DE LA MIGRATION
-- ============================================================

INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    '007',
    'Coeur des projets et validation des sources GitLab'
)
ON CONFLICT (version)
DO NOTHING;


COMMIT;