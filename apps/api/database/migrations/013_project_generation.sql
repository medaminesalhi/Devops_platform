BEGIN;

CREATE TABLE IF NOT EXISTS project_generation_runs (
    id BIGSERIAL PRIMARY KEY,

    project_id BIGINT NOT NULL,
    analysis_run_id BIGINT NOT NULL,
    environment_id BIGINT NOT NULL,

    status VARCHAR(30)
        NOT NULL DEFAULT 'pending',

    progress SMALLINT
        NOT NULL DEFAULT 0,

    current_step VARCHAR(100)
        NOT NULL DEFAULT 'pending',

    summary JSONB
        NOT NULL DEFAULT '{}'::JSONB,

    error_code VARCHAR(100),
    error_message TEXT,

    created_by BIGINT,

    created_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,

    CONSTRAINT project_generation_runs_project_fk
        FOREIGN KEY (project_id)
        REFERENCES projects (id)
        ON DELETE CASCADE,

    CONSTRAINT project_generation_runs_analysis_fk
        FOREIGN KEY (analysis_run_id)
        REFERENCES project_analysis_runs (id)
        ON DELETE RESTRICT,

    CONSTRAINT project_generation_runs_environment_fk
        FOREIGN KEY (environment_id)
        REFERENCES deployment_environments (id)
        ON DELETE RESTRICT,

    CONSTRAINT project_generation_runs_created_by_fk
        FOREIGN KEY (created_by)
        REFERENCES users (id)
        ON DELETE SET NULL,

    CONSTRAINT project_generation_runs_status_check
        CHECK (
            status IN (
                'pending',
                'running',
                'completed',
                'failed',
                'cancelled',
                'superseded'
            )
        ),

    CONSTRAINT project_generation_runs_progress_check
        CHECK (
            progress BETWEEN 0 AND 100
        )
);


CREATE UNIQUE INDEX IF NOT EXISTS
    project_generation_runs_one_active_per_project_idx
ON project_generation_runs (
    project_id
)
WHERE status IN (
    'pending',
    'running'
);


CREATE INDEX IF NOT EXISTS
    project_generation_runs_project_date_idx
ON project_generation_runs (
    project_id,
    created_at DESC
);


CREATE INDEX IF NOT EXISTS
    project_generation_runs_analysis_idx
ON project_generation_runs (
    analysis_run_id
);


CREATE TABLE IF NOT EXISTS project_generated_artifacts (
    id BIGSERIAL PRIMARY KEY,

    generation_run_id BIGINT NOT NULL,
    project_id BIGINT NOT NULL,
    component_id BIGINT,

    artifact_type VARCHAR(40) NOT NULL,
    relative_path TEXT NOT NULL,

    content TEXT NOT NULL,
    original_content TEXT,
    content_sha256 VARCHAR(64) NOT NULL,

    artifact_status VARCHAR(30)
        NOT NULL DEFAULT 'generated',

    review_status VARCHAR(30)
        NOT NULL DEFAULT 'pending_review',

    metadata JSONB
        NOT NULL DEFAULT '{}'::JSONB,

    created_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT project_generated_artifacts_run_fk
        FOREIGN KEY (generation_run_id)
        REFERENCES project_generation_runs (id)
        ON DELETE CASCADE,

    CONSTRAINT project_generated_artifacts_project_fk
        FOREIGN KEY (project_id)
        REFERENCES projects (id)
        ON DELETE CASCADE,

    CONSTRAINT project_generated_artifacts_component_fk
        FOREIGN KEY (component_id)
        REFERENCES project_components (id)
        ON DELETE SET NULL,

    CONSTRAINT project_generated_artifacts_type_check
        CHECK (
            artifact_type IN (
                'dockerfile',
                'dockerignore',
                'helm_chart',
                'helm_values',
                'helm_template',
                'gitops_manifest',
                'argocd_application'
            )
        ),

    CONSTRAINT project_generated_artifacts_status_check
        CHECK (
            artifact_status IN (
                'generated',
                'existing',
                'proposed_update',
                'needs_review'
            )
        ),

    CONSTRAINT project_generated_artifacts_review_check
        CHECK (
            review_status IN (
                'pending_review',
                'approved',
                'rejected'
            )
        ),

    CONSTRAINT project_generated_artifacts_run_path_unique
        UNIQUE (
            generation_run_id,
            relative_path
        )
);


CREATE INDEX IF NOT EXISTS
    project_generated_artifacts_run_idx
ON project_generated_artifacts (
    generation_run_id,
    artifact_type,
    relative_path
);


CREATE INDEX IF NOT EXISTS
    project_generated_artifacts_component_idx
ON project_generated_artifacts (
    component_id
);


CREATE TABLE IF NOT EXISTS project_generation_events (
    id BIGSERIAL PRIMARY KEY,

    generation_run_id BIGINT NOT NULL,

    level VARCHAR(20)
        NOT NULL DEFAULT 'info',

    step VARCHAR(100) NOT NULL,
    message TEXT NOT NULL,

    details JSONB
        NOT NULL DEFAULT '{}'::JSONB,

    created_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT project_generation_events_run_fk
        FOREIGN KEY (generation_run_id)
        REFERENCES project_generation_runs (id)
        ON DELETE CASCADE,

    CONSTRAINT project_generation_events_level_check
        CHECK (
            level IN (
                'info',
                'success',
                'warning',
                'error'
            )
        )
);


CREATE INDEX IF NOT EXISTS
    project_generation_events_run_idx
ON project_generation_events (
    generation_run_id,
    id
);


ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS generation_status VARCHAR(30)
        NOT NULL DEFAULT 'not_started',

    ADD COLUMN IF NOT EXISTS latest_generation_run_id BIGINT;


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'projects_generation_status_check'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT projects_generation_status_check
            CHECK (
                generation_status IN (
                    'not_started',
                    'pending',
                    'running',
                    'completed',
                    'failed'
                )
            );
    END IF;


    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'projects_latest_generation_run_fk'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT projects_latest_generation_run_fk
            FOREIGN KEY (
                latest_generation_run_id
            )
            REFERENCES project_generation_runs (id)
            ON DELETE SET NULL;
    END IF;
END;
$$;


INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    '013',
    'Phase 3 génération Docker Helm GitOps et Argo CD'
)
ON CONFLICT (version)
DO NOTHING;


COMMIT;