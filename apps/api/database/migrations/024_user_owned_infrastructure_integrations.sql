BEGIN;

-- ============================================================
-- CLOISONNEMENT MULTI-UTILISATEUR : INTÉGRATIONS + INFRASTRUCTURE
--
-- Les colonnes created_by existent déjà sur integration_connections
-- et deployment_environments. Cette migration transforme surtout les
-- contraintes d'unicité globales en contraintes par propriétaire.
--
-- Les lignes historiques avec created_by IS NULL ne sont attribuées à
-- personne automatiquement : elles restent visibles uniquement par un
-- administrateur dans l'application.
-- ============================================================


-- ============================================================
-- INTÉGRATIONS
-- ============================================================

DROP INDEX IF EXISTS
    integration_connections_name_lower_unique_idx;

CREATE UNIQUE INDEX IF NOT EXISTS
    integration_connections_owner_name_unique_idx
ON integration_connections (
    created_by,
    LOWER(name)
)
WHERE created_by IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS
    integration_connections_legacy_name_unique_idx
ON integration_connections (
    LOWER(name)
)
WHERE created_by IS NULL;

CREATE INDEX IF NOT EXISTS
    integration_connections_owner_provider_idx
ON integration_connections (
    created_by,
    provider_type,
    enabled
);


-- ============================================================
-- ENVIRONNEMENTS / INFRASTRUCTURE
-- ============================================================

DROP INDEX IF EXISTS
    deployment_environments_name_unique_idx;

DROP INDEX IF EXISTS
    deployment_environments_code_unique_idx;

CREATE UNIQUE INDEX IF NOT EXISTS
    deployment_environments_owner_name_unique_idx
ON deployment_environments (
    created_by,
    LOWER(name)
)
WHERE
    created_by IS NOT NULL
    AND configuration_status <> 'archived';

CREATE UNIQUE INDEX IF NOT EXISTS
    deployment_environments_owner_code_unique_idx
ON deployment_environments (
    created_by,
    LOWER(code)
)
WHERE
    created_by IS NOT NULL
    AND configuration_status <> 'archived';

CREATE UNIQUE INDEX IF NOT EXISTS
    deployment_environments_legacy_name_unique_idx
ON deployment_environments (
    LOWER(name)
)
WHERE
    created_by IS NULL
    AND configuration_status <> 'archived';

CREATE UNIQUE INDEX IF NOT EXISTS
    deployment_environments_legacy_code_unique_idx
ON deployment_environments (
    LOWER(code)
)
WHERE
    created_by IS NULL
    AND configuration_status <> 'archived';

CREATE INDEX IF NOT EXISTS
    deployment_environments_owner_status_idx
ON deployment_environments (
    created_by,
    configuration_status,
    environment_type
);

COMMIT;
