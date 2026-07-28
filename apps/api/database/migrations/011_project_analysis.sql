BEGIN;

CREATE TABLE IF NOT EXISTS project_analysis_runs (
    id BIGSERIAL PRIMARY KEY,

    project_id BIGINT NOT NULL,

    commit_policy VARCHAR(20)
        NOT NULL DEFAULT 'validated',

    requested_commit_sha VARCHAR(100),

    branch_head_sha VARCHAR(100),

    analyzed_commit_sha VARCHAR(100),

    selected_subdirectory TEXT,

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

    confirmed_by BIGINT,

    created_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    started_at TIMESTAMPTZ,

    finished_at TIMESTAMPTZ,

    confirmed_at TIMESTAMPTZ,

    CONSTRAINT project_analysis_runs_project_fk
        FOREIGN KEY (project_id)
        REFERENCES projects (id)
        ON DELETE CASCADE,

    CONSTRAINT project_analysis_runs_created_by_fk
        FOREIGN KEY (created_by)
        REFERENCES users (id)
        ON DELETE SET NULL,

    CONSTRAINT project_analysis_runs_confirmed_by_fk
        FOREIGN KEY (confirmed_by)
        REFERENCES users (id)
        ON DELETE SET NULL,

    CONSTRAINT project_analysis_runs_commit_policy_check
        CHECK (
            commit_policy IN (
                'validated',
                'latest'
            )
        ),

    CONSTRAINT project_analysis_runs_status_check
        CHECK (
            status IN (
                'pending',
                'preparing',
                'cloning',
                'analyzing',
                'completed',
                'failed',
                'cancelled',
                'confirmed'
            )
        ),

    CONSTRAINT project_analysis_runs_progress_check
        CHECK (
            progress BETWEEN 0 AND 100
        )
);


CREATE UNIQUE INDEX IF NOT EXISTS
    project_analysis_runs_one_active_per_project_idx
ON project_analysis_runs (
    project_id
)
WHERE status IN (
    'pending',
    'preparing',
    'cloning',
    'analyzing'
);


CREATE INDEX IF NOT EXISTS
    project_analysis_runs_project_date_idx
ON project_analysis_runs (
    project_id,
    created_at DESC
);


CREATE TABLE IF NOT EXISTS project_analysis_events (
    id BIGSERIAL PRIMARY KEY,

    analysis_run_id BIGINT NOT NULL,

    level VARCHAR(20)
        NOT NULL DEFAULT 'info',

    step VARCHAR(100)
        NOT NULL,

    message TEXT
        NOT NULL,

    details JSONB
        NOT NULL DEFAULT '{}'::JSONB,

    created_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT project_analysis_events_run_fk
        FOREIGN KEY (analysis_run_id)
        REFERENCES project_analysis_runs (id)
        ON DELETE CASCADE,

    CONSTRAINT project_analysis_events_level_check
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
    project_analysis_events_run_idx
ON project_analysis_events (
    analysis_run_id,
    id
);


CREATE TABLE IF NOT EXISTS project_components (
    id BIGSERIAL PRIMARY KEY,

    project_id BIGINT NOT NULL,

    analysis_run_id BIGINT NOT NULL,

    name VARCHAR(180) NOT NULL,

    component_type VARCHAR(50)
        NOT NULL DEFAULT 'unknown',

    root_path TEXT
        NOT NULL,

    runtime VARCHAR(100),

    framework VARCHAR(100),

    package_manager VARCHAR(50),

    build_command TEXT,

    start_command TEXT,

    detected_port INTEGER,

    deployable BOOLEAN
        NOT NULL DEFAULT TRUE,

    dockerfile_path TEXT,

    helm_chart_path TEXT,

    kubernetes_paths JSONB
        NOT NULL DEFAULT '[]'::JSONB,

    environment_variables JSONB
        NOT NULL DEFAULT '[]'::JSONB,

    confidence SMALLINT
        NOT NULL DEFAULT 0,

    configuration JSONB
        NOT NULL DEFAULT '{}'::JSONB,

    user_modified BOOLEAN
        NOT NULL DEFAULT FALSE,

    created_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT project_components_project_fk
        FOREIGN KEY (project_id)
        REFERENCES projects (id)
        ON DELETE CASCADE,

    CONSTRAINT project_components_analysis_fk
        FOREIGN KEY (analysis_run_id)
        REFERENCES project_analysis_runs (id)
        ON DELETE CASCADE,

    CONSTRAINT project_components_confidence_check
        CHECK (
            confidence BETWEEN 0 AND 100
        ),

    CONSTRAINT project_components_port_check
        CHECK (
            detected_port IS NULL
            OR detected_port BETWEEN 1 AND 65535
        )
);


CREATE UNIQUE INDEX IF NOT EXISTS
    project_components_run_root_name_idx
ON project_components (
    analysis_run_id,
    root_path,
    name
);


ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS analysis_status VARCHAR(30)
        NOT NULL DEFAULT 'not_started',

    ADD COLUMN IF NOT EXISTS latest_analysis_run_id BIGINT,

    ADD COLUMN IF NOT EXISTS analysis_confirmed_at TIMESTAMPTZ;


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname =
            'projects_analysis_status_check'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT
                projects_analysis_status_check
            CHECK (
                analysis_status IN (
                    'not_started',
                    'pending',
                    'running',
                    'completed',
                    'failed',
                    'confirmed'
                )
            );
    END IF;


    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname =
            'projects_latest_analysis_run_fk'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT
                projects_latest_analysis_run_fk
            FOREIGN KEY (
                latest_analysis_run_id
            )
            REFERENCES project_analysis_runs (id)
            ON DELETE SET NULL;
    END IF;
END;
$$;


INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    '011',
    'Phase 2 clone et analyse statique des projets'
)
ON CONFLICT (version)
DO NOTHING;


COMMIT;