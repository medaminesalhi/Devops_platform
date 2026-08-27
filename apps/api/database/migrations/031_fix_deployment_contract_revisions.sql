BEGIN;

-- ============================================================
-- CONTRATS DE DÉPLOIEMENT VERSIONNÉS
-- ============================================================
--
-- La migration 017 empêchait plusieurs contrats pour le même :
--   project_id + analysis_run_id + environment_id
--
-- Or le workflow supporte explicitement les révisions :
--   revision = MAX(revision) + 1
--
-- Une ancienne version peut être "superseded" et une nouvelle
-- version doit pouvoir être créée pour la même analyse.
-- ============================================================


ALTER TABLE project_deployment_contracts
DROP CONSTRAINT IF EXISTS project_deployment_contracts_unique;


ALTER TABLE project_deployment_contracts
ADD CONSTRAINT project_deployment_contracts_unique_revision
UNIQUE (
    project_id,
    analysis_run_id,
    environment_id,
    revision
);


-- Une seule révision confirmée doit rester active par projet.
-- Les anciennes révisions restent conservées avec
-- status = 'superseded'.
CREATE UNIQUE INDEX IF NOT EXISTS
    project_deployment_contracts_one_confirmed_per_project_idx
ON project_deployment_contracts (
    project_id
)
WHERE status = 'confirmed';


INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    '031',
    'Correction du versionnement des contrats de déploiement'
)
ON CONFLICT (version)
DO NOTHING;


COMMIT;
