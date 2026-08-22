BEGIN;

-- ============================================================
-- SApixi — séries temporelles k6 pour le mode Basic
--
-- Une ligne représente un snapshot agrégé (par défaut ~2 secondes),
-- pas une requête HTTP individuelle. Cela permet d'afficher les courbes
-- directement dans Angular sans Prometheus/Grafana.
-- ============================================================

DO $$
BEGIN
    IF to_regclass('public.performance_runs') IS NULL THEN
        RAISE EXCEPTION 'La table performance_runs est absente. Appliquez d''abord la migration 026.';
    END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS performance_run_samples (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL,

    sampled_at TIMESTAMPTZ NOT NULL,
    elapsed_seconds INTEGER NOT NULL,

    vus INTEGER NOT NULL DEFAULT 0,
    requests INTEGER NOT NULL DEFAULT 0,
    requests_total INTEGER NOT NULL DEFAULT 0,
    iterations_total INTEGER NOT NULL DEFAULT 0,

    rps DOUBLE PRECISION NOT NULL DEFAULT 0,
    avg_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
    p95_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
    p99_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
    error_rate_percent DOUBLE PRECISION NOT NULL DEFAULT 0,
    checks_rate_percent DOUBLE PRECISION NOT NULL DEFAULT 0,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT performance_run_samples_run_fk
        FOREIGN KEY (run_id)
        REFERENCES performance_runs (id)
        ON DELETE CASCADE,

    CONSTRAINT performance_run_samples_elapsed_check
        CHECK (elapsed_seconds >= 0),

    CONSTRAINT performance_run_samples_vus_check
        CHECK (vus >= 0),

    CONSTRAINT performance_run_samples_requests_check
        CHECK (requests >= 0 AND requests_total >= 0 AND iterations_total >= 0),

    CONSTRAINT performance_run_samples_percent_check
        CHECK (
            error_rate_percent >= 0
            AND error_rate_percent <= 100
            AND checks_rate_percent >= 0
            AND checks_rate_percent <= 100
        ),

    CONSTRAINT performance_run_samples_run_elapsed_unique
        UNIQUE (run_id, elapsed_seconds)
);

CREATE INDEX IF NOT EXISTS performance_run_samples_run_elapsed_idx
ON performance_run_samples (run_id, elapsed_seconds);

INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    '027',
    'Snapshots temporels k6 pour graphiques de performance intégrés'
)
ON CONFLICT (version)
DO NOTHING;

COMMIT;
