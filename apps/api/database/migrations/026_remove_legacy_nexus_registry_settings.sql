BEGIN;

-- Depuis le nouveau workflow, une connexion Nexus représente uniquement
-- l'accès à l'API Nexus. Les repositories Docker/Helm sont découverts
-- dynamiquement et sélectionnés au niveau projet/déploiement.
-- Ces deux colonnes provenaient de l'ancien modèle où un repository Docker
-- était attaché directement à la connexion Nexus.
ALTER TABLE integration_connections
    DROP COLUMN IF EXISTS registry_url;

ALTER TABLE integration_connections
    DROP COLUMN IF EXISTS registry_repository;

-- Efface uniquement les anciens états Nexus qui peuvent encore afficher
-- une fausse erreur du type "repository Docker / registre à configurer".
-- Le monitoring ou un test manuel recalculera immédiatement l'état réel.
UPDATE integration_connections
SET
    status = 'unchecked',
    consecutive_failures = 0,
    last_http_status = NULL,
    last_error = NULL,
    last_checked_at = NULL,
    last_latency_ms = NULL
WHERE
    provider_type = 'nexus'
    AND (
        status = 'not_configured'
        OR last_error ILIKE '%repository Docker%'
        OR last_error ILIKE '%registre Docker%'
    );

INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    '026',
    'Suppression des anciens paramètres repository/registry Nexus de la connexion'
)
ON CONFLICT (version)
DO NOTHING;

COMMIT;
