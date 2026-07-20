BEGIN;

-- ============================================================
-- TABLE DES INTÉGRATIONS
--
-- Cette table contient la configuration non sensible.
-- Les tokens et mots de passe restent dans le fichier .env.
-- ============================================================

CREATE TABLE IF NOT EXISTS integrations (
    id BIGSERIAL PRIMARY KEY,

    code VARCHAR(50) NOT NULL,
    name VARCHAR(100) NOT NULL,
    category VARCHAR(50) NOT NULL,

    base_url TEXT,
    username VARCHAR(150),

    secret_env_var VARCHAR(100),

    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    verify_ssl BOOLEAN NOT NULL DEFAULT TRUE,

    status VARCHAR(30) NOT NULL DEFAULT 'not_configured',

    last_http_status INTEGER,
    last_error TEXT,
    last_checked_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT integrations_code_unique
        UNIQUE (code),

    CONSTRAINT integrations_status_check
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
    integrations_status_idx
ON integrations (status);

CREATE INDEX IF NOT EXISTS
    integrations_enabled_idx
ON integrations (enabled);


-- ============================================================
-- FONCTION updated_at
-- ============================================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


DROP TRIGGER IF EXISTS
    integrations_updated_at_trigger
ON integrations;


CREATE TRIGGER integrations_updated_at_trigger
BEFORE UPDATE ON integrations
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();


-- ============================================================
-- INTÉGRATIONS DE LA PLATEFORME
-- ============================================================

INSERT INTO integrations (
    code,
    name,
    category,
    secret_env_var
)
VALUES
    (
        'gitlab',
        'GitLab',
        'Source et CI/CD',
        'GITLAB_TOKEN'
    ),
    (
        'nexus',
        'Nexus Repository',
        'Registry et artefacts',
        'NEXUS_PASSWORD'
    ),
    (
        'argocd',
        'Argo CD',
        'GitOps',
        'ARGOCD_TOKEN'
    ),
    (
        'kubernetes',
        'Kubernetes',
        'Infrastructure',
        'KUBERNETES_TOKEN'
    ),
    (
        'ollama',
        'Ollama',
        'Intelligence artificielle',
        NULL
    )
ON CONFLICT (code)
DO UPDATE SET
    name = EXCLUDED.name,
    category = EXCLUDED.category,
    secret_env_var = EXCLUDED.secret_env_var;


-- ============================================================
-- ENREGISTREMENT DE LA MIGRATION
-- ============================================================

INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    '003',
    'Création de la configuration des intégrations'
)
ON CONFLICT (version)
DO NOTHING;

COMMIT;