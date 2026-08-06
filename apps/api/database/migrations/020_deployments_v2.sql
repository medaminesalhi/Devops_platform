BEGIN;

-- ============================================================
-- SApixi — moteur de déploiement v2
--
-- Cette migration fait évoluer les anciennes tables
-- deployments / deployment_steps créées par la migration 002
-- et ajoute les journaux, composants, ressources, incidents,
-- diagnostics IA, conversation et corrections.
-- ============================================================

DO $$
BEGIN
    IF to_regclass('public.project_generation_runs') IS NULL THEN
        RAISE EXCEPTION
            'La table project_generation_runs est absente. Appliquez d''abord la migration 013.';
    END IF;
END;
$$;

-- ------------------------------------------------------------
-- DÉPLOIEMENTS
-- ------------------------------------------------------------

ALTER TABLE deployments
    DROP CONSTRAINT IF EXISTS deployments_status_check;

ALTER TABLE deployments
    ADD COLUMN IF NOT EXISTS generation_run_id BIGINT,
    ADD COLUMN IF NOT EXISTS version VARCHAR(160),
    ADD COLUMN IF NOT EXISTS sync_mode VARCHAR(30) NOT NULL DEFAULT 'confirm_before_sync',
    ADD COLUMN IF NOT EXISTS current_stage VARCHAR(40),
    ADD COLUMN IF NOT EXISTS current_stage_label VARCHAR(180) NOT NULL DEFAULT 'Brouillon',
    ADD COLUMN IF NOT EXISTS progress SMALLINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS note TEXT,
    ADD COLUMN IF NOT EXISTS gitops_commit VARCHAR(64),
    ADD COLUMN IF NOT EXISTS sync_confirmed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS error_code VARCHAR(120),
    ADD COLUMN IF NOT EXISTS error_message TEXT,
    ADD COLUMN IF NOT EXISTS cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS retry_of_deployment_id BIGINT,
    ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS locked_by VARCHAR(160),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;

UPDATE deployments
SET version = COALESCE(version, image_tag, commit_sha, 'deployment-' || id::TEXT)
WHERE version IS NULL;

UPDATE deployments
SET current_stage_label = CASE
        WHEN status = 'succeeded' THEN 'Déploiement terminé'
        WHEN status = 'failed' THEN 'Déploiement échoué'
        WHEN status = 'running' THEN 'Déploiement en cours'
        WHEN status = 'queued' THEN 'En attente du worker'
        WHEN status = 'cancelled' THEN 'Déploiement annulé'
        ELSE 'Brouillon'
    END
WHERE current_stage_label IS NULL OR current_stage_label = 'Brouillon';

ALTER TABLE deployments
    ADD CONSTRAINT deployments_status_check
    CHECK (
        status IN (
            'draft',
            'ready',
            'queued',
            'running',
            'waiting_confirmation',
            'succeeded',
            'failed',
            'cancelled'
        )
    );

ALTER TABLE deployments
    ADD CONSTRAINT deployments_sync_mode_check
    CHECK (
        sync_mode IN (
            'prepare_only',
            'confirm_before_sync',
            'automatic'
        )
    );

ALTER TABLE deployments
    ADD CONSTRAINT deployments_progress_check
    CHECK (progress BETWEEN 0 AND 100);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'deployments_generation_run_fk'
    ) THEN
        ALTER TABLE deployments
            ADD CONSTRAINT deployments_generation_run_fk
            FOREIGN KEY (generation_run_id)
            REFERENCES project_generation_runs (id)
            ON DELETE RESTRICT;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'deployments_retry_of_fk'
    ) THEN
        ALTER TABLE deployments
            ADD CONSTRAINT deployments_retry_of_fk
            FOREIGN KEY (retry_of_deployment_id)
            REFERENCES deployments (id)
            ON DELETE SET NULL;
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS deployments_generation_idx
ON deployments (generation_run_id);

CREATE INDEX IF NOT EXISTS deployments_project_environment_idx
ON deployments (project_id, environment_id, created_at DESC);

CREATE INDEX IF NOT EXISTS deployments_queue_idx
ON deployments (status, created_at)
WHERE status = 'queued';

-- ------------------------------------------------------------
-- ÉTAPES
-- ------------------------------------------------------------

ALTER TABLE deployment_steps
    DROP CONSTRAINT IF EXISTS deployment_steps_status_check;

UPDATE deployment_steps
SET status = 'pending'
WHERE status = 'waiting';

ALTER TABLE deployment_steps
    ADD COLUMN IF NOT EXISTS stage VARCHAR(40),
    ADD COLUMN IF NOT EXISTS description TEXT,
    ADD COLUMN IF NOT EXISTS details JSONB NOT NULL DEFAULT '{}'::JSONB,
    ADD COLUMN IF NOT EXISTS error_code VARCHAR(120),
    ADD COLUMN IF NOT EXISTS error_message TEXT,
    ADD COLUMN IF NOT EXISTS duration_seconds INTEGER;

UPDATE deployment_steps
SET stage = COALESCE(stage, code)
WHERE stage IS NULL;

ALTER TABLE deployment_steps
    ADD CONSTRAINT deployment_steps_status_check
    CHECK (
        status IN (
            'pending',
            'running',
            'succeeded',
            'failed',
            'skipped',
            'cancelled'
        )
    );

CREATE UNIQUE INDEX IF NOT EXISTS deployment_steps_key_unique_idx
ON deployment_steps (deployment_id, code);

-- ------------------------------------------------------------
-- COMPOSANTS DE RELEASE
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS deployment_components (
    id BIGSERIAL PRIMARY KEY,
    deployment_id BIGINT NOT NULL,
    component_id BIGINT,
    component_key VARCHAR(120) NOT NULL,
    name VARCHAR(180) NOT NULL,
    component_type VARCHAR(80) NOT NULL DEFAULT 'application',
    root_path TEXT NOT NULL DEFAULT '.',
    dockerfile_path TEXT,
    image_repository TEXT NOT NULL,
    image_tag VARCHAR(160) NOT NULL,
    image_digest VARCHAR(180),
    port INTEGER,
    replicas INTEGER NOT NULL DEFAULT 1,
    build_status VARCHAR(30) NOT NULL DEFAULT 'pending',
    registry_status VARCHAR(30) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT deployment_components_deployment_fk
        FOREIGN KEY (deployment_id)
        REFERENCES deployments (id)
        ON DELETE CASCADE,

    CONSTRAINT deployment_components_component_fk
        FOREIGN KEY (component_id)
        REFERENCES project_components (id)
        ON DELETE SET NULL,

    CONSTRAINT deployment_components_port_check
        CHECK (port IS NULL OR port BETWEEN 1 AND 65535),

    CONSTRAINT deployment_components_replicas_check
        CHECK (replicas BETWEEN 0 AND 100),

    CONSTRAINT deployment_components_build_status_check
        CHECK (build_status IN ('pending', 'running', 'succeeded', 'failed', 'skipped', 'cancelled')),

    CONSTRAINT deployment_components_registry_status_check
        CHECK (registry_status IN ('pending', 'running', 'succeeded', 'failed', 'skipped', 'cancelled')),

    CONSTRAINT deployment_components_key_unique
        UNIQUE (deployment_id, component_key)
);

CREATE INDEX IF NOT EXISTS deployment_components_deployment_idx
ON deployment_components (deployment_id, id);

-- ------------------------------------------------------------
-- LOGS TEMPS RÉEL
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS deployment_logs (
    id BIGSERIAL PRIMARY KEY,
    deployment_id BIGINT NOT NULL,
    step_id BIGINT,
    scope VARCHAR(30) NOT NULL DEFAULT 'system',
    level VARCHAR(20) NOT NULL DEFAULT 'info',
    component_name VARCHAR(180),
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT deployment_logs_deployment_fk
        FOREIGN KEY (deployment_id)
        REFERENCES deployments (id)
        ON DELETE CASCADE,

    CONSTRAINT deployment_logs_step_fk
        FOREIGN KEY (step_id)
        REFERENCES deployment_steps (id)
        ON DELETE SET NULL,

    CONSTRAINT deployment_logs_scope_check
        CHECK (scope IN ('system', 'docker', 'nexus', 'gitops', 'argocd', 'kubernetes', 'application')),

    CONSTRAINT deployment_logs_level_check
        CHECK (level IN ('info', 'warning', 'error', 'success'))
);

CREATE INDEX IF NOT EXISTS deployment_logs_stream_idx
ON deployment_logs (deployment_id, id);

-- ------------------------------------------------------------
-- RESSOURCES OBSERVÉES
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS deployment_resources (
    id BIGSERIAL PRIMARY KEY,
    deployment_id BIGINT NOT NULL,
    resource_key VARCHAR(300) NOT NULL,
    kind VARCHAR(40) NOT NULL,
    name VARCHAR(253) NOT NULL,
    namespace VARCHAR(120) NOT NULL,
    status VARCHAR(120) NOT NULL DEFAULT 'Unknown',
    health VARCHAR(30) NOT NULL DEFAULT 'unknown',
    ready VARCHAR(40),
    image TEXT,
    restarts INTEGER,
    age VARCHAR(80) NOT NULL DEFAULT '—',
    message TEXT,
    url TEXT,
    raw JSONB NOT NULL DEFAULT '{}'::JSONB,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT deployment_resources_deployment_fk
        FOREIGN KEY (deployment_id)
        REFERENCES deployments (id)
        ON DELETE CASCADE,

    CONSTRAINT deployment_resources_health_check
        CHECK (health IN ('healthy', 'progressing', 'degraded', 'unknown')),

    CONSTRAINT deployment_resources_unique
        UNIQUE (deployment_id, resource_key)
);

CREATE INDEX IF NOT EXISTS deployment_resources_deployment_idx
ON deployment_resources (deployment_id, kind, name);

-- ------------------------------------------------------------
-- INCIDENTS
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS deployment_incidents (
    id BIGSERIAL PRIMARY KEY,
    deployment_id BIGINT NOT NULL,
    step_id BIGINT,
    code VARCHAR(120) NOT NULL,
    title VARCHAR(220) NOT NULL,
    message TEXT NOT NULL,
    stage VARCHAR(40) NOT NULL,
    component_name VARCHAR(180),
    integration_name VARCHAR(180),
    retryable BOOLEAN NOT NULL DEFAULT FALSE,
    requires_new_generation BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_at TIMESTAMPTZ,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT deployment_incidents_deployment_fk
        FOREIGN KEY (deployment_id)
        REFERENCES deployments (id)
        ON DELETE CASCADE,

    CONSTRAINT deployment_incidents_step_fk
        FOREIGN KEY (step_id)
        REFERENCES deployment_steps (id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS deployment_incidents_current_idx
ON deployment_incidents (deployment_id, occurred_at DESC);

-- ------------------------------------------------------------
-- DIAGNOSTIC IA
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS deployment_diagnostics (
    id BIGSERIAL PRIMARY KEY,
    deployment_id BIGINT NOT NULL UNIQUE,
    status VARCHAR(30) NOT NULL DEFAULT 'idle',
    cause TEXT,
    explanation TEXT,
    confidence VARCHAR(20),
    target_phase VARCHAR(30),
    evidence JSONB NOT NULL DEFAULT '[]'::JSONB,
    provider_connection_id BIGINT,
    model VARCHAR(255),
    raw_response JSONB,
    error_message TEXT,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT deployment_diagnostics_deployment_fk
        FOREIGN KEY (deployment_id)
        REFERENCES deployments (id)
        ON DELETE CASCADE,

    CONSTRAINT deployment_diagnostics_provider_fk
        FOREIGN KEY (provider_connection_id)
        REFERENCES integration_connections (id)
        ON DELETE SET NULL,

    CONSTRAINT deployment_diagnostics_status_check
        CHECK (status IN ('idle', 'running', 'completed', 'failed')),

    CONSTRAINT deployment_diagnostics_confidence_check
        CHECK (confidence IS NULL OR confidence IN ('low', 'medium', 'high')),

    CONSTRAINT deployment_diagnostics_phase_check
        CHECK (target_phase IS NULL OR target_phase IN ('integration', 'analysis', 'proposal', 'generation', 'deployment'))
);

CREATE TABLE IF NOT EXISTS deployment_chat_messages (
    id BIGSERIAL PRIMARY KEY,
    deployment_id BIGINT NOT NULL,
    role VARCHAR(20) NOT NULL,
    content TEXT NOT NULL,
    created_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT deployment_chat_deployment_fk
        FOREIGN KEY (deployment_id)
        REFERENCES deployments (id)
        ON DELETE CASCADE,

    CONSTRAINT deployment_chat_user_fk
        FOREIGN KEY (created_by)
        REFERENCES users (id)
        ON DELETE SET NULL,

    CONSTRAINT deployment_chat_role_check
        CHECK (role IN ('assistant', 'user', 'system'))
);

CREATE INDEX IF NOT EXISTS deployment_chat_stream_idx
ON deployment_chat_messages (deployment_id, id);

CREATE TABLE IF NOT EXISTS deployment_corrections (
    id BIGSERIAL PRIMARY KEY,
    deployment_id BIGINT NOT NULL,
    title VARCHAR(220) NOT NULL,
    summary TEXT NOT NULL,
    target_phase VARCHAR(30) NOT NULL,
    target_file TEXT,
    diff TEXT,
    risk VARCHAR(20) NOT NULL DEFAULT 'medium',
    status VARCHAR(30) NOT NULL DEFAULT 'proposed',
    approved_by BIGINT,
    approved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT deployment_corrections_deployment_fk
        FOREIGN KEY (deployment_id)
        REFERENCES deployments (id)
        ON DELETE CASCADE,

    CONSTRAINT deployment_corrections_approved_by_fk
        FOREIGN KEY (approved_by)
        REFERENCES users (id)
        ON DELETE SET NULL,

    CONSTRAINT deployment_corrections_phase_check
        CHECK (target_phase IN ('integration', 'analysis', 'proposal', 'generation', 'deployment')),

    CONSTRAINT deployment_corrections_risk_check
        CHECK (risk IN ('low', 'medium', 'high')),

    CONSTRAINT deployment_corrections_status_check
        CHECK (status IN ('proposed', 'approved', 'rejected', 'applied'))
);

CREATE INDEX IF NOT EXISTS deployment_corrections_deployment_idx
ON deployment_corrections (deployment_id, id);

-- ------------------------------------------------------------
-- TRIGGER updated_at
-- ------------------------------------------------------------

DROP TRIGGER IF EXISTS deployments_updated_at_trigger ON deployments;
CREATE TRIGGER deployments_updated_at_trigger
BEFORE UPDATE ON deployments
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS deployment_components_updated_at_trigger ON deployment_components;
CREATE TRIGGER deployment_components_updated_at_trigger
BEFORE UPDATE ON deployment_components
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS deployment_diagnostics_updated_at_trigger ON deployment_diagnostics;
CREATE TRIGGER deployment_diagnostics_updated_at_trigger
BEFORE UPDATE ON deployment_diagnostics
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS deployment_corrections_updated_at_trigger ON deployment_corrections;
CREATE TRIGGER deployment_corrections_updated_at_trigger
BEFORE UPDATE ON deployment_corrections
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

INSERT INTO schema_migrations (version, description)
VALUES ('020', 'Moteur de déploiement, logs, ressources et diagnostic IA')
ON CONFLICT (version) DO NOTHING;

COMMIT;
