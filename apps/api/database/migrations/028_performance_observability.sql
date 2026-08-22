BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM schema_migrations
        WHERE version = '028'
    ) THEN
        NULL;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS performance_observability_stacks (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    created_by BIGINT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    kubernetes_connection_id BIGINT NOT NULL REFERENCES integration_connections(id) ON DELETE RESTRICT,

    namespace VARCHAR(63) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'provisioning', 'ready', 'failed', 'deleting', 'deleted')),

    retention_days INTEGER NOT NULL DEFAULT 7
        CHECK (retention_days BETWEEN 1 AND 365),
    prometheus_storage_size VARCHAR(32) NOT NULL DEFAULT '8Gi',
    grafana_storage_size VARCHAR(32) NOT NULL DEFAULT '2Gi',
    storage_class_name VARCHAR(253),

    ingress_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ingress_class_name VARCHAR(100),
    grafana_host VARCHAR(253),
    grafana_tls_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    grafana_tls_secret_name VARCHAR(253),

    prometheus_release_name VARCHAR(100) NOT NULL DEFAULT 'sapixi-k6-prometheus',
    grafana_release_name VARCHAR(100) NOT NULL DEFAULT 'sapixi-k6-grafana',

    prometheus_remote_write_url TEXT,
    prometheus_query_url TEXT,
    grafana_base_url TEXT,
    grafana_dashboard_uid VARCHAR(120) NOT NULL DEFAULT 'k6-performance',

    grafana_admin_user VARCHAR(100) NOT NULL DEFAULT 'admin',
    grafana_admin_password_ciphertext TEXT,

    worker_name VARCHAR(255),
    locked_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,

    error_code VARCHAR(120),
    error_message TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS performance_observability_stacks_project_idx
    ON performance_observability_stacks(project_id, created_at DESC);

CREATE INDEX IF NOT EXISTS performance_observability_stacks_status_idx
    ON performance_observability_stacks(status, created_at ASC);

CREATE INDEX IF NOT EXISTS performance_observability_stacks_connection_idx
    ON performance_observability_stacks(kubernetes_connection_id);

CREATE UNIQUE INDEX IF NOT EXISTS performance_observability_stacks_active_namespace_uidx
    ON performance_observability_stacks(project_id, namespace)
    WHERE status <> 'deleted';

DROP TRIGGER IF EXISTS performance_observability_stacks_updated_at_trigger
    ON performance_observability_stacks;

CREATE TRIGGER performance_observability_stacks_updated_at_trigger
BEFORE UPDATE ON performance_observability_stacks
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS performance_observability_logs (
    id BIGSERIAL PRIMARY KEY,
    stack_id BIGINT NOT NULL REFERENCES performance_observability_stacks(id) ON DELETE CASCADE,
    level VARCHAR(20) NOT NULL DEFAULT 'info'
        CHECK (level IN ('info', 'success', 'warning', 'error')),
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS performance_observability_logs_stack_idx
    ON performance_observability_logs(stack_id, id ASC);

INSERT INTO schema_migrations (version, description)
VALUES (
    '028',
    'Provisioning asynchrone Prometheus + Grafana pour les tests k6'
)
ON CONFLICT (version) DO NOTHING;

COMMIT;
