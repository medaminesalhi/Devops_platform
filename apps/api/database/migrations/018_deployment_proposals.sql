BEGIN;

CREATE TABLE IF NOT EXISTS project_deployment_proposals (
    id BIGSERIAL PRIMARY KEY,

    project_id BIGINT NOT NULL,
    analysis_run_id BIGINT NOT NULL,
    environment_id BIGINT NOT NULL,
    contract_id BIGINT,

    status VARCHAR(30) NOT NULL DEFAULT 'preparing',
    mode VARCHAR(30) NOT NULL DEFAULT 'hybrid',

    ai_connection_id BIGINT,
    ai_model VARCHAR(255),

    decisions JSONB NOT NULL DEFAULT '{}'::JSONB,
    components JSONB NOT NULL DEFAULT '[]'::JSONB,
    questions JSONB NOT NULL DEFAULT '[]'::JSONB,
    answers JSONB NOT NULL DEFAULT '{}'::JSONB,
    warnings JSONB NOT NULL DEFAULT '[]'::JSONB,
    validation JSONB NOT NULL DEFAULT '{}'::JSONB,
    ai_raw_response JSONB,

    created_by BIGINT,
    updated_by BIGINT,
    confirmed_by BIGINT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    confirmed_at TIMESTAMPTZ,

    CONSTRAINT project_deployment_proposals_project_fk
        FOREIGN KEY (project_id)
        REFERENCES projects (id)
        ON DELETE CASCADE,

    CONSTRAINT project_deployment_proposals_analysis_fk
        FOREIGN KEY (analysis_run_id)
        REFERENCES project_analysis_runs (id)
        ON DELETE RESTRICT,

    CONSTRAINT project_deployment_proposals_environment_fk
        FOREIGN KEY (environment_id)
        REFERENCES deployment_environments (id)
        ON DELETE RESTRICT,

    CONSTRAINT project_deployment_proposals_ai_connection_fk
        FOREIGN KEY (ai_connection_id)
        REFERENCES integration_connections (id)
        ON DELETE SET NULL,

    CONSTRAINT project_deployment_proposals_created_by_fk
        FOREIGN KEY (created_by)
        REFERENCES users (id)
        ON DELETE SET NULL,

    CONSTRAINT project_deployment_proposals_updated_by_fk
        FOREIGN KEY (updated_by)
        REFERENCES users (id)
        ON DELETE SET NULL,

    CONSTRAINT project_deployment_proposals_confirmed_by_fk
        FOREIGN KEY (confirmed_by)
        REFERENCES users (id)
        ON DELETE SET NULL,

    CONSTRAINT project_deployment_proposals_status_check
        CHECK (
            status IN (
                'preparing',
                'needs_input',
                'ready',
                'confirmed',
                'failed'
            )
        ),

    CONSTRAINT project_deployment_proposals_mode_check
        CHECK (
            mode IN (
                'hybrid',
                'deterministic'
            )
        )
);

CREATE INDEX IF NOT EXISTS
    project_deployment_proposals_project_date_idx
ON project_deployment_proposals (
    project_id,
    created_at DESC
);

CREATE INDEX IF NOT EXISTS
    project_deployment_proposals_analysis_idx
ON project_deployment_proposals (
    analysis_run_id
);

CREATE INDEX IF NOT EXISTS
    project_deployment_proposals_environment_idx
ON project_deployment_proposals (
    environment_id
);

CREATE INDEX IF NOT EXISTS
    project_deployment_proposals_status_idx
ON project_deployment_proposals (
    status
);

DO $$
BEGIN
    IF to_regclass('public.project_deployment_contracts') IS NULL THEN
        RAISE EXCEPTION
            'La table project_deployment_contracts est absente. Appliquez d''abord la migration 017_project_workflow_v2.sql.';
    END IF;
END;
$$;

ALTER TABLE project_deployment_contracts
    ADD COLUMN IF NOT EXISTS proposal_id BIGINT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'project_deployment_contracts_proposal_fk'
    ) THEN
        ALTER TABLE project_deployment_contracts
            ADD CONSTRAINT project_deployment_contracts_proposal_fk
            FOREIGN KEY (proposal_id)
            REFERENCES project_deployment_proposals (id)
            ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'project_deployment_proposals_contract_fk'
    ) THEN
        ALTER TABLE project_deployment_proposals
            ADD CONSTRAINT project_deployment_proposals_contract_fk
            FOREIGN KEY (contract_id)
            REFERENCES project_deployment_contracts (id)
            ON DELETE SET NULL;
    END IF;
END;
$$;

INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    '018',
    'Propositions de déploiement assistées par IA'
)
ON CONFLICT (version)
DO NOTHING;

COMMIT;