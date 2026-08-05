BEGIN;

-- ============================================================
-- SUPPRESSION DU CONCEPT CLIENT
--
-- Les environnements et intégrations deviennent globaux à
-- l'installation SApixi. Les projets restent reliés directement
-- aux environnements.
-- ============================================================


-- ============================================================
-- VÉRIFICATION DES DOUBLONS
--
-- Avant de supprimer client_id, deux clients différents
-- pourraient théoriquement avoir un environnement portant
-- le même nom ou le même code.
--
-- Dans ce cas, la migration s'arrête afin d'éviter une perte
-- ou une modification automatique des données.
-- ============================================================

DO $$
BEGIN
    IF EXISTS (
        SELECT
            LOWER(name)

        FROM deployment_environments

        WHERE configuration_status <> 'archived'

        GROUP BY LOWER(name)

        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION
            'Plusieurs environnements actifs portent le même nom. Renommez-les avant la migration 016.';
    END IF;


    IF EXISTS (
        SELECT
            LOWER(code)

        FROM deployment_environments

        WHERE configuration_status <> 'archived'

        GROUP BY LOWER(code)

        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION
            'Plusieurs environnements actifs portent le même code. Renommez-les avant la migration 016.';
    END IF;
END;
$$;


-- ============================================================
-- PROJETS
--
-- Le module Projet n'utilise déjà plus la notion de client.
-- ============================================================

ALTER TABLE projects
    DROP COLUMN IF EXISTS client_id;


-- ============================================================
-- INTÉGRATIONS
--
-- Toutes les intégrations deviennent globales.
-- ============================================================

DROP INDEX IF EXISTS
    integration_connections_client_idx;


ALTER TABLE integration_connections
    DROP COLUMN IF EXISTS client_id,
    DROP COLUMN IF EXISTS scope;


-- ============================================================
-- ENVIRONNEMENTS
--
-- Supprimer les contraintes basées sur client_id.
-- ============================================================

DROP INDEX IF EXISTS
    deployment_environments_name_unique_idx;


DROP INDEX IF EXISTS
    deployment_environments_client_idx;


ALTER TABLE deployment_environments
    DROP CONSTRAINT IF EXISTS
        deployment_environments_code_unique,

    DROP CONSTRAINT IF EXISTS
        deployment_environments_client_fk,

    DROP COLUMN IF EXISTS client_id;


-- ============================================================
-- NOUVELLE UNICITÉ GLOBALE
--
-- Un environnement actif doit avoir un nom et un code uniques
-- dans toute l'installation SApixi.
--
-- Un environnement archivé ne bloque pas la réutilisation
-- future de son nom.
-- ============================================================

CREATE UNIQUE INDEX IF NOT EXISTS
    deployment_environments_name_unique_idx

ON deployment_environments (
    LOWER(name)
)

WHERE configuration_status <> 'archived';


CREATE UNIQUE INDEX IF NOT EXISTS
    deployment_environments_code_unique_idx

ON deployment_environments (
    LOWER(code)
)

WHERE configuration_status <> 'archived';


-- ============================================================
-- TABLES CLIENTS
-- ============================================================

DROP TABLE IF EXISTS
    client_memberships;


DROP TABLE IF EXISTS
    clients;


-- ============================================================
-- HISTORIQUE DE MIGRATION
-- ============================================================

INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    '016',
    'Suppression du concept client dans les integrations et environnements'
)
ON CONFLICT (version)
DO NOTHING;


COMMIT;