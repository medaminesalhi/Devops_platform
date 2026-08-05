BEGIN;

-- ============================================================
-- PHASES 2, 3 ET 4 DU PARCOURS PROJET
--
-- Phase 2 :
--   contrat de déploiement complété et confirmé.
--
-- Phase 3 :
--   plan IA structuré et génération déterministe.
--
-- Phase 4 :
--   validation, édition et approbation humaine.
-- ============================================================


-- ============================================================
-- CONTRATS DE DÉPLOIEMENT
-- ============================================================

CREATE TABLE IF NOT EXISTS project_deployment_contracts (
    id BIGSERIAL PRIMARY KEY,

    project_id BIGINT NOT NULL,
    analysis_run_id BIGINT NOT NULL,
    environment_id BIGINT NOT NULL,

    status VARCHAR(30)
        NOT NULL DEFAULT 'draft',

    revision INTEGER
        NOT NULL DEFAULT 1,

    namespace VARCHAR(63) NOT NULL,
    domain VARCHAR(255),

    contract JSONB
        NOT NULL DEFAULT '{}'::JSONB,

    validation JSONB
        NOT NULL DEFAULT '{}'::JSONB,

    created_by BIGINT,
    updated_by BIGINT,
    confirmed_by BIGINT,

    created_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    confirmed_at TIMESTAMPTZ,

    CONSTRAINT project_deployment_contracts_project_fk
        FOREIGN KEY (project_id)
        REFERENCES projects (id)
        ON DELETE CASCADE,

    CONSTRAINT project_deployment_contracts_analysis_fk
        FOREIGN KEY (analysis_run_id)
        REFERENCES project_analysis_runs (id)
        ON DELETE RESTRICT,

    CONSTRAINT project_deployment_contracts_environment_fk
        FOREIGN KEY (environment_id)
        REFERENCES deployment_environments (id)
        ON DELETE RESTRICT,

    CONSTRAINT project_deployment_contracts_created_by_fk
        FOREIGN KEY (created_by)
        REFERENCES users (id)
        ON DELETE SET NULL,

    CONSTRAINT project_deployment_contracts_updated_by_fk
        FOREIGN KEY (updated_by)
        REFERENCES users (id)
        ON DELETE SET NULL,

    CONSTRAINT project_deployment_contracts_confirmed_by_fk
        FOREIGN KEY (confirmed_by)
        REFERENCES users (id)
        ON DELETE SET NULL,

    CONSTRAINT project_deployment_contracts_status_check
        CHECK (
            status IN (
                'draft',
                'confirmed',
                'superseded'
            )
        ),

    CONSTRAINT project_deployment_contracts_revision_check
        CHECK (
            revision > 0
        ),

    CONSTRAINT project_deployment_contracts_unique
        UNIQUE (
            project_id,
            analysis_run_id,
            environment_id
        )
);


CREATE INDEX IF NOT EXISTS
    project_deployment_contracts_project_idx

ON project_deployment_contracts (
    project_id,
    updated_at DESC
);


-- ============================================================
-- EXÉCUTIONS IA
--
-- La table ne stocke pas les vraies valeurs des secrets.
-- Elle conserve le fournisseur, le modèle, le résumé de la
-- requête, la réponse JSON et les éventuelles erreurs.
-- ============================================================

CREATE TABLE IF NOT EXISTS project_ai_runs (
    id BIGSERIAL PRIMARY KEY,

    project_id BIGINT NOT NULL,

    contract_id BIGINT,
    generation_run_id BIGINT,
    connection_id BIGINT,

    provider_type VARCHAR(40),
    model_identifier VARCHAR(255),

    run_type VARCHAR(40) NOT NULL,
    prompt_version VARCHAR(40) NOT NULL,

    status VARCHAR(30)
        NOT NULL DEFAULT 'pending',

    request_summary JSONB
        NOT NULL DEFAULT '{}'::JSONB,

    response_json JSONB,

    latency_ms INTEGER,

    error_code VARCHAR(100),
    error_message TEXT,

    created_by BIGINT,

    created_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,

    CONSTRAINT project_ai_runs_project_fk
        FOREIGN KEY (project_id)
        REFERENCES projects (id)
        ON DELETE CASCADE,

    CONSTRAINT project_ai_runs_contract_fk
        FOREIGN KEY (contract_id)
        REFERENCES project_deployment_contracts (id)
        ON DELETE SET NULL,

    CONSTRAINT project_ai_runs_generation_fk
        FOREIGN KEY (generation_run_id)
        REFERENCES project_generation_runs (id)
        ON DELETE SET NULL,

    CONSTRAINT project_ai_runs_connection_fk
        FOREIGN KEY (connection_id)
        REFERENCES integration_connections (id)
        ON DELETE SET NULL,

    CONSTRAINT project_ai_runs_created_by_fk
        FOREIGN KEY (created_by)
        REFERENCES users (id)
        ON DELETE SET NULL,

    CONSTRAINT project_ai_runs_type_check
        CHECK (
            run_type IN (
                'generation_plan',
                'artifact_revision',
                'deployment_diagnosis'
            )
        ),

    CONSTRAINT project_ai_runs_status_check
        CHECK (
            status IN (
                'pending',
                'running',
                'completed',
                'failed'
            )
        ),

    CONSTRAINT project_ai_runs_latency_check
        CHECK (
            latency_ms IS NULL
            OR latency_ms >= 0
        )
);


CREATE INDEX IF NOT EXISTS
    project_ai_runs_project_idx

ON project_ai_runs (
    project_id,
    created_at DESC
);


-- ============================================================
-- EXTENSION DES GÉNÉRATIONS EXISTANTES
-- ============================================================

ALTER TABLE project_generation_runs
    ADD COLUMN IF NOT EXISTS contract_id BIGINT,

    ADD COLUMN IF NOT EXISTS ai_run_id BIGINT,

    ADD COLUMN IF NOT EXISTS ai_connection_id BIGINT,

    ADD COLUMN IF NOT EXISTS ai_model VARCHAR(255),

    ADD COLUMN IF NOT EXISTS generation_mode VARCHAR(30)
        NOT NULL DEFAULT 'hybrid',

    ADD COLUMN IF NOT EXISTS prompt_version VARCHAR(40),

    ADD COLUMN IF NOT EXISTS confirmed_by BIGINT,

    ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ;


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1

        FROM pg_constraint

        WHERE conname =
            'project_generation_runs_contract_fk'
    ) THEN
        ALTER TABLE project_generation_runs

        ADD CONSTRAINT
            project_generation_runs_contract_fk

        FOREIGN KEY (contract_id)

        REFERENCES project_deployment_contracts (id)

        ON DELETE RESTRICT;
    END IF;


    IF NOT EXISTS (
        SELECT 1

        FROM pg_constraint

        WHERE conname =
            'project_generation_runs_ai_run_fk'
    ) THEN
        ALTER TABLE project_generation_runs

        ADD CONSTRAINT
            project_generation_runs_ai_run_fk

        FOREIGN KEY (ai_run_id)

        REFERENCES project_ai_runs (id)

        ON DELETE SET NULL;
    END IF;


    IF NOT EXISTS (
        SELECT 1

        FROM pg_constraint

        WHERE conname =
            'project_generation_runs_ai_connection_fk'
    ) THEN
        ALTER TABLE project_generation_runs

        ADD CONSTRAINT
            project_generation_runs_ai_connection_fk

        FOREIGN KEY (ai_connection_id)

        REFERENCES integration_connections (id)

        ON DELETE SET NULL;
    END IF;


    IF NOT EXISTS (
        SELECT 1

        FROM pg_constraint

        WHERE conname =
            'project_generation_runs_confirmed_by_fk'
    ) THEN
        ALTER TABLE project_generation_runs

        ADD CONSTRAINT
            project_generation_runs_confirmed_by_fk

        FOREIGN KEY (confirmed_by)

        REFERENCES users (id)

        ON DELETE SET NULL;
    END IF;
END;
$$;


ALTER TABLE project_generation_runs
DROP CONSTRAINT IF EXISTS
    project_generation_runs_status_check;


ALTER TABLE project_generation_runs
ADD CONSTRAINT
    project_generation_runs_status_check

CHECK (
    status IN (
        'pending',
        'running',
        'completed',
        'awaiting_review',
        'confirmed',
        'failed',
        'cancelled',
        'superseded'
    )
);


ALTER TABLE project_generation_runs
DROP CONSTRAINT IF EXISTS
    project_generation_runs_generation_mode_check;


ALTER TABLE project_generation_runs
ADD CONSTRAINT
    project_generation_runs_generation_mode_check

CHECK (
    generation_mode IN (
        'hybrid',
        'deterministic'
    )
);


-- ============================================================
-- VALIDATION ET REVUE DES ARTEFACTS
-- ============================================================

ALTER TABLE project_generated_artifacts
    ADD COLUMN IF NOT EXISTS validation_status VARCHAR(30)
        NOT NULL DEFAULT 'pending',

    ADD COLUMN IF NOT EXISTS validation_messages JSONB
        NOT NULL DEFAULT '[]'::JSONB,

    ADD COLUMN IF NOT EXISTS review_comment TEXT,

    ADD COLUMN IF NOT EXISTS reviewed_by BIGINT,

    ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ,

    ADD COLUMN IF NOT EXISTS edited_by BIGINT,

    ADD COLUMN IF NOT EXISTS edited_at TIMESTAMPTZ;


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1

        FROM pg_constraint

        WHERE conname =
            'project_generated_artifacts_reviewed_by_fk'
    ) THEN
        ALTER TABLE project_generated_artifacts

        ADD CONSTRAINT
            project_generated_artifacts_reviewed_by_fk

        FOREIGN KEY (reviewed_by)

        REFERENCES users (id)

        ON DELETE SET NULL;
    END IF;


    IF NOT EXISTS (
        SELECT 1

        FROM pg_constraint

        WHERE conname =
            'project_generated_artifacts_edited_by_fk'
    ) THEN
        ALTER TABLE project_generated_artifacts

        ADD CONSTRAINT
            project_generated_artifacts_edited_by_fk

        FOREIGN KEY (edited_by)

        REFERENCES users (id)

        ON DELETE SET NULL;
    END IF;
END;
$$;


ALTER TABLE project_generated_artifacts
DROP CONSTRAINT IF EXISTS
    project_generated_artifacts_type_check;


ALTER TABLE project_generated_artifacts
ADD CONSTRAINT
    project_generated_artifacts_type_check

CHECK (
    artifact_type IN (
        'dockerfile',
        'dockerignore',
        'helm_chart',
        'helm_values',
        'helm_template',
        'configmap',
        'secret_template',
        'migration_job',
        'gitops_manifest',
        'argocd_project',
        'argocd_application'
    )
);


ALTER TABLE project_generated_artifacts
DROP CONSTRAINT IF EXISTS
    project_generated_artifacts_validation_check;


ALTER TABLE project_generated_artifacts
ADD CONSTRAINT
    project_generated_artifacts_validation_check

CHECK (
    validation_status IN (
        'pending',
        'passed',
        'warning',
        'failed'
    )
);


-- ============================================================
-- ÉTAT GLOBAL DU PROJET
-- ============================================================

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS
        deployment_contract_status VARCHAR(30)
        NOT NULL DEFAULT 'not_started',

    ADD COLUMN IF NOT EXISTS
        latest_deployment_contract_id BIGINT;


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1

        FROM pg_constraint

        WHERE conname =
            'projects_latest_deployment_contract_fk'
    ) THEN
        ALTER TABLE projects

        ADD CONSTRAINT
            projects_latest_deployment_contract_fk

        FOREIGN KEY (
            latest_deployment_contract_id
        )

        REFERENCES project_deployment_contracts (id)

        ON DELETE SET NULL;
    END IF;
END;
$$;


ALTER TABLE projects
DROP CONSTRAINT IF EXISTS
    projects_deployment_contract_status_check;


ALTER TABLE projects
ADD CONSTRAINT
    projects_deployment_contract_status_check

CHECK (
    deployment_contract_status IN (
        'not_started',
        'draft',
        'confirmed'
    )
);


ALTER TABLE projects
DROP CONSTRAINT IF EXISTS
    projects_generation_status_check;


ALTER TABLE projects
ADD CONSTRAINT
    projects_generation_status_check

CHECK (
    generation_status IN (
        'not_started',
        'pending',
        'running',
        'completed',
        'awaiting_review',
        'confirmed',
        'failed'
    )
);


-- ============================================================
-- HISTORIQUE DE MIGRATION
-- ============================================================

INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    '017',
    'Contrat de déploiement, génération IA hybride et revue humaine'
)
ON CONFLICT (version)
DO NOTHING;

COMMIT;