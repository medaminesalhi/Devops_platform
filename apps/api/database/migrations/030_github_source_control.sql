BEGIN;

-- Autoriser GitHub comme fournisseur d'intégration.
ALTER TABLE integration_connections
DROP CONSTRAINT IF EXISTS integration_connections_provider_check;

ALTER TABLE integration_connections
ADD CONSTRAINT integration_connections_provider_check
CHECK (
    provider_type IN (
        'gitlab',
        'github',
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

-- Un projet Git peut maintenant provenir de GitLab ou GitHub.
ALTER TABLE projects
DROP CONSTRAINT IF EXISTS projects_source_provider_check;

ALTER TABLE projects
ADD CONSTRAINT projects_source_provider_check
CHECK (
    source_provider IN (
        'gitlab',
        'github',
        'archive'
    )
);

INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    '030',
    'Support GitHub comme fournisseur de code source'
)
ON CONFLICT (version)
DO NOTHING;

COMMIT;
