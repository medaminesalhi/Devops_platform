BEGIN;

ALTER TABLE environment_connections
DROP CONSTRAINT IF EXISTS environment_connections_role_check;

ALTER TABLE environment_connections
ADD CONSTRAINT environment_connections_role_check
CHECK (
    service_role IN (
        'kubernetes',
        'argocd',
        'container_registry',
        'gitops_repository',
        'storage',
        'ai_provider',
        'custom_http_service'
    )
);

INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    '015',
    'Ajout du stockage NFS et du service HTTP aux environnements'
)
ON CONFLICT (version)
DO NOTHING;

COMMIT;