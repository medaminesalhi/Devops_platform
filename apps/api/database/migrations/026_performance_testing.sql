BEGIN;

-- ============================================================
-- SApixi — tests de performance k6
--
-- Deux niveaux sont conservés séparément :
--   1. performance_tests : définition réutilisable d'un test ;
--   2. performance_runs  : photographie immuable d'une exécution.
--
-- Les métriques agrégées finales sont conservées dans PostgreSQL.
-- Les séries temporelles détaillées restent optionnelles et peuvent
-- être envoyées par k6 vers Prometheus en mode observability.
-- ============================================================

DO $$
BEGIN
    IF to_regclass('public.projects') IS NULL THEN
        RAISE EXCEPTION 'La table projects est absente.';
    END IF;

    IF to_regclass('public.deployments') IS NULL THEN
        RAISE EXCEPTION 'La table deployments est absente.';
    END IF;

    IF to_regclass('public.users') IS NULL THEN
        RAISE EXCEPTION 'La table users est absente.';
    END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS performance_tests (
    id BIGSERIAL PRIMARY KEY,

    project_id BIGINT NOT NULL,
    deployment_id BIGINT,
    created_by BIGINT NOT NULL,

    name VARCHAR(180) NOT NULL,
    description TEXT,
    target_url TEXT NOT NULL,

    test_type VARCHAR(30) NOT NULL,
    mode VARCHAR(30) NOT NULL,

    load_profile JSONB NOT NULL DEFAULT '{}'::JSONB,
    thresholds JSONB NOT NULL DEFAULT '{}'::JSONB,
    observability JSONB,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT performance_tests_project_fk
        FOREIGN KEY (project_id)
        REFERENCES projects (id)
        ON DELETE CASCADE,

    CONSTRAINT performance_tests_deployment_fk
        FOREIGN KEY (deployment_id)
        REFERENCES deployments (id)
        ON DELETE SET NULL,

    CONSTRAINT performance_tests_created_by_fk
        FOREIGN KEY (created_by)
        REFERENCES users (id)
        ON DELETE RESTRICT,

    CONSTRAINT performance_tests_type_check
        CHECK (test_type IN ('smoke', 'load', 'stress', 'spike', 'soak', 'custom')),

    CONSTRAINT performance_tests_mode_check
        CHECK (mode IN ('basic', 'observability')),

    CONSTRAINT performance_tests_name_check
        CHECK (CHAR_LENGTH(BTRIM(name)) BETWEEN 1 AND 180)
);

CREATE INDEX IF NOT EXISTS performance_tests_project_updated_idx
ON performance_tests (project_id, updated_at DESC)
WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS performance_tests_owner_updated_idx
ON performance_tests (created_by, updated_at DESC)
WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS performance_tests_deployment_idx
ON performance_tests (deployment_id, updated_at DESC)
WHERE deployment_id IS NOT NULL;

DROP TRIGGER IF EXISTS performance_tests_updated_at_trigger
ON performance_tests;

CREATE TRIGGER performance_tests_updated_at_trigger
BEFORE UPDATE ON performance_tests
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();


CREATE TABLE IF NOT EXISTS performance_runs (
    id BIGSERIAL PRIMARY KEY,

    test_id BIGINT NOT NULL,
    project_id BIGINT NOT NULL,
    deployment_id BIGINT,
    created_by BIGINT NOT NULL,

    test_name VARCHAR(180) NOT NULL,
    target_url TEXT NOT NULL,
    test_type VARCHAR(30) NOT NULL,
    mode VARCHAR(30) NOT NULL,

    -- Snapshot de la configuration au moment du lancement.
    load_profile JSONB NOT NULL DEFAULT '{}'::JSONB,
    thresholds JSONB NOT NULL DEFAULT '{}'::JSONB,
    observability JSONB,

    status VARCHAR(30) NOT NULL DEFAULT 'queued',
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,

    worker_name VARCHAR(180),
    locked_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,

    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    exit_code INTEGER,

    metrics JSONB,
    threshold_results JSONB,
    summary JSONB,

    grafana_dashboard_url TEXT,

    error_code VARCHAR(120),
    error_message TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT performance_runs_test_fk
        FOREIGN KEY (test_id)
        REFERENCES performance_tests (id)
        ON DELETE CASCADE,

    CONSTRAINT performance_runs_project_fk
        FOREIGN KEY (project_id)
        REFERENCES projects (id)
        ON DELETE CASCADE,

    CONSTRAINT performance_runs_deployment_fk
        FOREIGN KEY (deployment_id)
        REFERENCES deployments (id)
        ON DELETE SET NULL,

    CONSTRAINT performance_runs_created_by_fk
        FOREIGN KEY (created_by)
        REFERENCES users (id)
        ON DELETE RESTRICT,

    CONSTRAINT performance_runs_status_check
        CHECK (status IN ('queued', 'running', 'passed', 'failed', 'cancelled')),

    CONSTRAINT performance_runs_type_check
        CHECK (test_type IN ('smoke', 'load', 'stress', 'spike', 'soak', 'custom')),

    CONSTRAINT performance_runs_mode_check
        CHECK (mode IN ('basic', 'observability'))
);

CREATE INDEX IF NOT EXISTS performance_runs_queue_idx
ON performance_runs (created_at, id)
WHERE status = 'queued';

CREATE INDEX IF NOT EXISTS performance_runs_project_created_idx
ON performance_runs (project_id, created_at DESC);

CREATE INDEX IF NOT EXISTS performance_runs_owner_created_idx
ON performance_runs (created_by, created_at DESC);

CREATE INDEX IF NOT EXISTS performance_runs_deployment_created_idx
ON performance_runs (deployment_id, created_at DESC)
WHERE deployment_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS performance_runs_status_created_idx
ON performance_runs (status, created_at DESC);

DROP TRIGGER IF EXISTS performance_runs_updated_at_trigger
ON performance_runs;

CREATE TRIGGER performance_runs_updated_at_trigger
BEFORE UPDATE ON performance_runs
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();


CREATE TABLE IF NOT EXISTS performance_run_logs (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL,
    level VARCHAR(20) NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT performance_run_logs_run_fk
        FOREIGN KEY (run_id)
        REFERENCES performance_runs (id)
        ON DELETE CASCADE,

    CONSTRAINT performance_run_logs_level_check
        CHECK (level IN ('info', 'success', 'warning', 'error'))
);

CREATE INDEX IF NOT EXISTS performance_run_logs_run_created_idx
ON performance_run_logs (run_id, created_at, id);


INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    '026',
    'Tests de performance k6, runs asynchrones et résultats agrégés'
)
ON CONFLICT (version)
DO NOTHING;

COMMIT;
