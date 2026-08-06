BEGIN;

-- ============================================================
-- PROJETS EN BROUILLON
--
-- Un projet est maintenant créé dès la première étape de
-- l'assistant, avant la saisie de sa source Git ou ZIP.
--
-- repository_url doit donc pouvoir rester NULL tant que
-- le projet est en statut draft.
--
-- La route d'activation vérifiera ensuite qu'une source valide
-- est réellement enregistrée avant de passer le projet en actif.
-- ============================================================

ALTER TABLE projects
    ALTER COLUMN repository_url DROP NOT NULL;


-- ============================================================
-- ENREGISTREMENT DE LA MIGRATION
-- ============================================================

INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    '019',
    'Autoriser les projets brouillons sans source'
)
ON CONFLICT (version)
DO NOTHING;

COMMIT;