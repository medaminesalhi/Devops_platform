BEGIN;

-- ============================================================
-- PIXIMIND — gestion des comptes, validation admin et audit
-- ============================================================

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS company VARCHAR(180),
    ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS approved_by BIGINT,
    ADD COLUMN IF NOT EXISTS rejected_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS rejection_reason TEXT,
    ADD COLUMN IF NOT EXISTS suspended_at TIMESTAMPTZ;

UPDATE users
SET status = CASE
    WHEN is_active = TRUE THEN 'active'
    ELSE 'suspended'
END
WHERE status IS NULL
   OR status NOT IN ('pending', 'active', 'rejected', 'suspended');

UPDATE users
SET status = 'suspended',
    suspended_at = COALESCE(suspended_at, CURRENT_TIMESTAMP)
WHERE is_active = FALSE
  AND status = 'active'
  AND approved_at IS NULL
  AND rejected_at IS NULL
  AND suspended_at IS NULL;

UPDATE users
SET approved_at = COALESCE(approved_at, created_at)
WHERE status = 'active';

ALTER TABLE users
    DROP CONSTRAINT IF EXISTS users_status_check;

ALTER TABLE users
    ADD CONSTRAINT users_status_check
    CHECK (
        status IN (
            'pending',
            'active',
            'rejected',
            'suspended'
        )
    );

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'users_approved_by_fk'
    ) THEN
        ALTER TABLE users
            ADD CONSTRAINT users_approved_by_fk
            FOREIGN KEY (approved_by)
            REFERENCES users (id)
            ON DELETE SET NULL;
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS users_status_created_idx
ON users (status, created_at DESC);

-- ============================================================
-- HISTORIQUE DES CONNEXIONS
-- ============================================================

CREATE TABLE IF NOT EXISTS auth_login_history (
    id BIGSERIAL PRIMARY KEY,

    user_id BIGINT,
    identifier VARCHAR(255) NOT NULL,

    success BOOLEAN NOT NULL,
    failure_reason VARCHAR(80),

    ip_address VARCHAR(64),
    user_agent TEXT,

    logged_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT auth_login_history_user_fk
        FOREIGN KEY (user_id)
        REFERENCES users (id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS auth_login_history_user_idx
ON auth_login_history (user_id, logged_at DESC);

CREATE INDEX IF NOT EXISTS auth_login_history_logged_at_idx
ON auth_login_history (logged_at DESC);

-- ============================================================
-- JOURNAL D'AUDIT
-- ============================================================

CREATE TABLE IF NOT EXISTS audit_logs (
    id BIGSERIAL PRIMARY KEY,

    actor_user_id BIGINT,
    action VARCHAR(100) NOT NULL,

    resource_type VARCHAR(80) NOT NULL,
    resource_id VARCHAR(160),

    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT audit_logs_actor_user_fk
        FOREIGN KEY (actor_user_id)
        REFERENCES users (id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS audit_logs_actor_idx
ON audit_logs (actor_user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS audit_logs_resource_idx
ON audit_logs (resource_type, resource_id, created_at DESC);

CREATE INDEX IF NOT EXISTS audit_logs_created_at_idx
ON audit_logs (created_at DESC);

-- ============================================================
-- ENREGISTRER LA MIGRATION
-- ============================================================

INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    '022',
    'Validation admin des comptes, paramètres utilisateur et audit'
)
ON CONFLICT (version)
DO NOTHING;

COMMIT;
