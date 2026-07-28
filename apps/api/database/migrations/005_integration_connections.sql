BEGIN;

-- ============================================================
-- CONNEXIONS AUX SERVICES EXTERNES
--
-- Une ligne représente une vraie connexion :
-- GitLab Piximind, Cluster Lab, Nexus Principal, etc.
--
-- Plusieurs connexions peuvent avoir le même fournisseur.
-- ============================================================

CREATE TABLE IF NOT EXISTS integration_connections (
    id BIGSERIAL PRIMARY KEY,

    name VARCHAR(120) NOT NULL,
    provider_type VARCHAR(40) NOT NULL,

    base_url TEXT NOT NULL,
    environment VARCHAR(80) NOT NULL DEFAULT 'internal',
    description TEXT,

    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    verify_ssl BOOLEAN NOT NULL DEFAULT TRUE,

    monitoring_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    check_interval_seconds INTEGER NOT NULL DEFAULT 300,
    failure_threshold SMALLINT NOT NULL DEFAULT 3,

    status VARCHAR(30) NOT NULL DEFAULT 'unchecked',
    consecutive_failures INTEGER NOT NULL DEFAULT 0,

    last_http_status INTEGER,
    last_error TEXT,
    last_checked_at TIMESTAMPTZ,
    last_latency_ms INTEGER,

    created_by BIGINT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT integration_connections_provider_check
        CHECK (
            provider_type IN (
                'gitlab',
                'nexus',
                'argocd',
                'kubernetes',
                'ollama',
                'generic_http'
            )
        ),

    CONSTRAINT integration_connections_status_check
        CHECK (
            status IN (
                'not_configured',
                'unchecked',
                'online',
                'degraded',
                'offline'
            )
        ),

    CONSTRAINT integration_connections_interval_check
        CHECK (
            check_interval_seconds
            BETWEEN 60 AND 86400
        ),

    CONSTRAINT integration_connections_failure_threshold_check
        CHECK (
            failure_threshold
            BETWEEN 1 AND 10
        ),

    CONSTRAINT integration_connections_failures_check
        CHECK (
            consecutive_failures >= 0
        ),

    CONSTRAINT integration_connections_created_by_fk
        FOREIGN KEY (created_by)
        REFERENCES users (id)
        ON DELETE SET NULL
);


CREATE UNIQUE INDEX IF NOT EXISTS
    integration_connections_name_lower_unique_idx
ON integration_connections (
    LOWER(name)
);


CREATE INDEX IF NOT EXISTS
    integration_connections_provider_idx
ON integration_connections (
    provider_type
);


CREATE INDEX IF NOT EXISTS
    integration_connections_status_idx
ON integration_connections (
    status
);


CREATE INDEX IF NOT EXISTS
    integration_connections_monitoring_idx
ON integration_connections (
    monitoring_enabled,
    enabled,
    last_checked_at
);


-- ============================================================
-- CREDENTIALS
--
-- Le token ou mot de passe est stocké sous forme chiffrée.
-- Angular ne recevra jamais secret_ciphertext.
-- ============================================================

CREATE TABLE IF NOT EXISTS integration_credentials (
    id BIGSERIAL PRIMARY KEY,

    connection_id BIGINT NOT NULL,

    auth_type VARCHAR(30) NOT NULL DEFAULT 'none',
    username VARCHAR(200),

    secret_ciphertext TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT integration_credentials_connection_unique
        UNIQUE (connection_id),

    CONSTRAINT integration_credentials_connection_fk
        FOREIGN KEY (connection_id)
        REFERENCES integration_connections (id)
        ON DELETE CASCADE,

    CONSTRAINT integration_credentials_auth_type_check
        CHECK (
            auth_type IN (
                'none',
                'token',
                'basic'
            )
        )
);


-- ============================================================
-- HISTORIQUE DES CONTRÔLES
-- ============================================================

CREATE TABLE IF NOT EXISTS integration_health_checks (
    id BIGSERIAL PRIMARY KEY,

    connection_id BIGINT NOT NULL,

    status VARCHAR(30) NOT NULL,

    http_status INTEGER,
    latency_ms INTEGER,

    server_reachable BOOLEAN NOT NULL DEFAULT FALSE,
    authenticated BOOLEAN,

    checked_url TEXT,
    message TEXT,

    checked_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT integration_health_checks_connection_fk
        FOREIGN KEY (connection_id)
        REFERENCES integration_connections (id)
        ON DELETE CASCADE,

    CONSTRAINT integration_health_checks_status_check
        CHECK (
            status IN (
                'not_configured',
                'unchecked',
                'online',
                'degraded',
                'offline'
            )
        )
);


CREATE INDEX IF NOT EXISTS
    integration_health_checks_connection_idx
ON integration_health_checks (
    connection_id,
    checked_at DESC
);


-- ============================================================
-- NOTIFICATIONS
-- ============================================================

CREATE TABLE IF NOT EXISTS notifications (
    id BIGSERIAL PRIMARY KEY,

    connection_id BIGINT,

    notification_type VARCHAR(50) NOT NULL,
    severity VARCHAR(20) NOT NULL,

    title VARCHAR(200) NOT NULL,
    message TEXT NOT NULL,

    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT notifications_connection_fk
        FOREIGN KEY (connection_id)
        REFERENCES integration_connections (id)
        ON DELETE SET NULL,

    CONSTRAINT notifications_severity_check
        CHECK (
            severity IN (
                'info',
                'warning',
                'critical',
                'success'
            )
        )
);


CREATE INDEX IF NOT EXISTS
    notifications_unread_idx
ON notifications (
    created_at DESC
)
WHERE read_at IS NULL;


-- ============================================================
-- HISTORIQUE DES ACTIONS
-- ============================================================

CREATE TABLE IF NOT EXISTS integration_activity_logs (
    id BIGSERIAL PRIMARY KEY,

    connection_id BIGINT,
    user_id BIGINT,

    action VARCHAR(100) NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT integration_activity_connection_fk
        FOREIGN KEY (connection_id)
        REFERENCES integration_connections (id)
        ON DELETE CASCADE,

    CONSTRAINT integration_activity_user_fk
        FOREIGN KEY (user_id)
        REFERENCES users (id)
        ON DELETE SET NULL
);


CREATE INDEX IF NOT EXISTS
    integration_activity_connection_idx
ON integration_activity_logs (
    connection_id,
    created_at DESC
);


-- ============================================================
-- MISE À JOUR AUTOMATIQUE DE updated_at
-- ============================================================

CREATE OR REPLACE FUNCTION
    set_integration_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


DROP TRIGGER IF EXISTS
    integration_connections_updated_at_trigger
ON integration_connections;


CREATE TRIGGER
    integration_connections_updated_at_trigger
BEFORE UPDATE ON integration_connections
FOR EACH ROW
EXECUTE FUNCTION
    set_integration_updated_at();


DROP TRIGGER IF EXISTS
    integration_credentials_updated_at_trigger
ON integration_credentials;


CREATE TRIGGER
    integration_credentials_updated_at_trigger
BEFORE UPDATE ON integration_credentials
FOR EACH ROW
EXECUTE FUNCTION
    set_integration_updated_at();


-- ============================================================
-- ENREGISTREMENT DE LA MIGRATION
-- ============================================================

INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    '005',
    'Nouvelle architecture multi-connexions des intégrations'
)
ON CONFLICT (version)
DO NOTHING;


COMMIT;