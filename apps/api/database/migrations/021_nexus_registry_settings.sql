BEGIN;

ALTER TABLE integration_connections
    ADD COLUMN IF NOT EXISTS registry_url TEXT;

ALTER TABLE integration_connections
    ADD COLUMN IF NOT EXISTS registry_repository VARCHAR(200);

INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    '021',
    'Séparation API Nexus et registre Docker'
)
ON CONFLICT (version)
DO NOTHING;

COMMIT;