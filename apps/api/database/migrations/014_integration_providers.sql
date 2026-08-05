BEGIN;

ALTER TABLE integration_connections
DROP CONSTRAINT IF EXISTS integration_connections_provider_check;

ALTER TABLE integration_connections
ADD CONSTRAINT integration_connections_provider_check
CHECK (
    provider_type IN (
        'gitlab',
        'nexus',
        'argocd',
        'kubernetes',
        'nfs',
        'ollama',
        'litellm',
        'vllm',
        'openai_compatible',
        'generic_http'
    )
);

INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    '014',
    'Fournisseurs IA, NFS et TLS configurable pour les integrations'
)
ON CONFLICT (version)
DO NOTHING;

COMMIT;