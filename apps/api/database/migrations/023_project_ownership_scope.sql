BEGIN;

-- ============================================================
-- CLOISONNEMENT DES PROJETS PAR PROPRIÉTAIRE
-- ============================================================
--
-- La colonne projects.created_by existe déjà depuis la migration 002.
-- Cette migration ajoute l'index nécessaire aux nouvelles requêtes qui
-- filtrent systématiquement les projets par utilisateur.
--
-- Les anciens projets dont created_by est NULL ne sont pas réattribués
-- automatiquement : ils resteront visibles uniquement par les admins.
-- Cela évite d'attribuer arbitrairement un projet historique au mauvais
-- utilisateur.
-- ============================================================

CREATE INDEX IF NOT EXISTS
    projects_created_by_updated_at_idx
ON projects (
    created_by,
    updated_at DESC
)
WHERE archived_at IS NULL;

CREATE INDEX IF NOT EXISTS
    deployments_project_created_at_idx
ON deployments (
    project_id,
    created_at DESC
);

COMMIT;
