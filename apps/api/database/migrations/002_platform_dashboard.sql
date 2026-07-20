BEGIN;

-- ============================================================
-- TABLE DES PROJETS
-- ============================================================

CREATE TABLE IF NOT EXISTS projects (
    id BIGSERIAL PRIMARY KEY,

    name VARCHAR(120) NOT NULL,
    slug VARCHAR(120) NOT NULL,

    repository_url TEXT NOT NULL,
    default_branch VARCHAR(120) NOT NULL DEFAULT 'main',

    framework VARCHAR(80),

    status VARCHAR(30) NOT NULL DEFAULT 'draft',

    created_by BIGINT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT projects_slug_unique
        UNIQUE (slug),

    CONSTRAINT projects_status_check
        CHECK (
            status IN (
                'draft',
                'active',
                'paused',
                'error',
                'archived'
            )
        ),

    CONSTRAINT projects_created_by_fk
        FOREIGN KEY (created_by)
        REFERENCES users (id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS
    projects_status_idx
ON projects (status);

CREATE INDEX IF NOT EXISTS
    projects_created_at_idx
ON projects (created_at DESC);


-- ============================================================
-- TABLE DES DÉPLOIEMENTS
-- ============================================================

CREATE TABLE IF NOT EXISTS deployments (
    id BIGSERIAL PRIMARY KEY,

    project_id BIGINT NOT NULL,

    environment VARCHAR(40) NOT NULL DEFAULT 'lab',

    status VARCHAR(30) NOT NULL DEFAULT 'queued',

    commit_sha VARCHAR(64),
    image_tag VARCHAR(160),

    triggered_by BIGINT,

    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT deployments_project_fk
        FOREIGN KEY (project_id)
        REFERENCES projects (id)
        ON DELETE CASCADE,

    CONSTRAINT deployments_triggered_by_fk
        FOREIGN KEY (triggered_by)
        REFERENCES users (id)
        ON DELETE SET NULL,

    CONSTRAINT deployments_status_check
        CHECK (
            status IN (
                'queued',
                'running',
                'succeeded',
                'failed',
                'cancelled'
            )
        )
);

CREATE INDEX IF NOT EXISTS
    deployments_project_idx
ON deployments (project_id);

CREATE INDEX IF NOT EXISTS
    deployments_status_idx
ON deployments (status);

CREATE INDEX IF NOT EXISTS
    deployments_created_at_idx
ON deployments (created_at DESC);


-- ============================================================
-- ÉTAPES D’UN DÉPLOIEMENT
-- ============================================================

CREATE TABLE IF NOT EXISTS deployment_steps (
    id BIGSERIAL PRIMARY KEY,

    deployment_id BIGINT NOT NULL,

    step_order SMALLINT NOT NULL,

    code VARCHAR(80) NOT NULL,
    name VARCHAR(120) NOT NULL,

    status VARCHAR(30) NOT NULL DEFAULT 'waiting',

    message TEXT,

    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT deployment_steps_deployment_fk
        FOREIGN KEY (deployment_id)
        REFERENCES deployments (id)
        ON DELETE CASCADE,

    CONSTRAINT deployment_steps_unique_order
        UNIQUE (
            deployment_id,
            step_order
        ),

    CONSTRAINT deployment_steps_status_check
        CHECK (
            status IN (
                'waiting',
                'running',
                'succeeded',
                'failed',
                'skipped'
            )
        )
);

CREATE INDEX IF NOT EXISTS
    deployment_steps_deployment_idx
ON deployment_steps (deployment_id);


-- ============================================================
-- MISE À JOUR AUTOMATIQUE DE updated_at
-- ============================================================

CREATE OR REPLACE FUNCTION
    update_updated_at_column()
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
    update_updated_at_column();


-- ============================================================
-- ENREGISTRER LA MIGRATION
-- ============================================================

INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    '002',
    'Création des projets, déploiements et étapes'
)
ON CONFLICT (version)
DO NOTHING;

COMMIT;