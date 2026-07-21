BEGIN;

-- ============================================================
-- CLIENTS
--
-- Un client représente une organisation :
-- Piximind, Client A, Client B, etc.
-- ============================================================

CREATE TABLE IF NOT EXISTS clients (
    id BIGSERIAL PRIMARY KEY,

    name VARCHAR(120) NOT NULL,
    slug VARCHAR(120) NOT NULL,

    status VARCHAR(30) NOT NULL DEFAULT 'active',

    created_by BIGINT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT clients_slug_unique
        UNIQUE (slug),

    CONSTRAINT clients_status_check
        CHECK (
            status IN (
                'active',
                'suspended',
                'archived'
            )
        ),

    CONSTRAINT clients_created_by_fk
        FOREIGN KEY (created_by)
        REFERENCES users (id)
        ON DELETE SET NULL
);


-- ============================================================
-- MEMBRES D'UN CLIENT
--
-- Cette table limite les utilisateurs aux clients
-- auxquels ils sont autorisés à accéder.
-- ============================================================

CREATE TABLE IF NOT EXISTS client_memberships (
    id BIGSERIAL PRIMARY KEY,

    client_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,

    membership_role VARCHAR(30)
        NOT NULL DEFAULT 'viewer',

    created_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT client_memberships_unique
        UNIQUE (
            client_id,
            user_id
        ),

    CONSTRAINT client_memberships_client_fk
        FOREIGN KEY (client_id)
        REFERENCES clients (id)
        ON DELETE CASCADE,

    CONSTRAINT client_memberships_user_fk
        FOREIGN KEY (user_id)
        REFERENCES users (id)
        ON DELETE CASCADE,

    CONSTRAINT client_memberships_role_check
        CHECK (
            membership_role IN (
                'owner',
                'admin',
                'devops',
                'developer',
                'viewer'
            )
        )
);


-- ============================================================
-- AJOUT DU CLIENT AUX CONNEXIONS
--
-- scope = global :
-- connexion utilisable par plusieurs clients.
--
-- scope = client :
-- connexion appartenant uniquement à un client.
-- ============================================================

ALTER TABLE integration_connections
    ADD COLUMN IF NOT EXISTS client_id BIGINT
        REFERENCES clients (id)
        ON DELETE SET NULL;


ALTER TABLE integration_connections
    ADD COLUMN IF NOT EXISTS scope VARCHAR(20)
        NOT NULL DEFAULT 'global';


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname =
            'integration_connections_scope_check'
    ) THEN
        ALTER TABLE integration_connections
            ADD CONSTRAINT
                integration_connections_scope_check
            CHECK (
                scope IN (
                    'global',
                    'client'
                )
            );
    END IF;
END;
$$;


CREATE INDEX IF NOT EXISTS
    integration_connections_client_idx
ON integration_connections (
    client_id
);


-- ============================================================
-- ENVIRONNEMENTS DE DÉPLOIEMENT
--
-- Exemple :
-- Piximind Lab
-- Client A Production
-- Client A Kubernetes B
-- ============================================================

CREATE TABLE IF NOT EXISTS deployment_environments (
    id BIGSERIAL PRIMARY KEY,

    client_id BIGINT NOT NULL,

    name VARCHAR(140) NOT NULL,
    code VARCHAR(120) NOT NULL,

    environment_type VARCHAR(30)
        NOT NULL DEFAULT 'lab',

    description TEXT,

    namespace VARCHAR(120) NOT NULL,
    domain VARCHAR(255),

    configuration_status VARCHAR(30)
        NOT NULL DEFAULT 'active',

    is_default BOOLEAN NOT NULL DEFAULT FALSE,

    created_by BIGINT,

    created_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT deployment_environments_client_fk
        FOREIGN KEY (client_id)
        REFERENCES clients (id)
        ON DELETE CASCADE,

    CONSTRAINT deployment_environments_created_by_fk
        FOREIGN KEY (created_by)
        REFERENCES users (id)
        ON DELETE SET NULL,

    CONSTRAINT deployment_environments_type_check
        CHECK (
            environment_type IN (
                'lab',
                'staging',
                'production',
                'custom'
            )
        ),

    CONSTRAINT deployment_environments_status_check
        CHECK (
            configuration_status IN (
                'draft',
                'active',
                'archived'
            )
        ),

    CONSTRAINT deployment_environments_code_unique
        UNIQUE (
            client_id,
            code
        )
);


CREATE UNIQUE INDEX IF NOT EXISTS
    deployment_environments_name_unique_idx
ON deployment_environments (
    client_id,
    LOWER(name)
);


CREATE INDEX IF NOT EXISTS
    deployment_environments_client_idx
ON deployment_environments (
    client_id
);


CREATE INDEX IF NOT EXISTS
    deployment_environments_type_idx
ON deployment_environments (
    environment_type
);


-- ============================================================
-- SERVICES ASSOCIÉS À UN ENVIRONNEMENT
--
-- Cette table relie un environnement aux connexions.
-- ============================================================

CREATE TABLE IF NOT EXISTS environment_connections (
    id BIGSERIAL PRIMARY KEY,

    environment_id BIGINT NOT NULL,
    connection_id BIGINT NOT NULL,

    service_role VARCHAR(40) NOT NULL,

    is_required BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT environment_connections_environment_fk
        FOREIGN KEY (environment_id)
        REFERENCES deployment_environments (id)
        ON DELETE CASCADE,

    CONSTRAINT environment_connections_connection_fk
        FOREIGN KEY (connection_id)
        REFERENCES integration_connections (id)
        ON DELETE RESTRICT,

    CONSTRAINT environment_connections_unique_role
        UNIQUE (
            environment_id,
            service_role
        ),

    CONSTRAINT environment_connections_role_check
        CHECK (
            service_role IN (
                'kubernetes',
                'argocd',
                'container_registry',
                'gitops_repository',
                'ai_provider'
            )
        )
);


CREATE INDEX IF NOT EXISTS
    environment_connections_environment_idx
ON environment_connections (
    environment_id
);


CREATE INDEX IF NOT EXISTS
    environment_connections_connection_idx
ON environment_connections (
    connection_id
);


-- ============================================================
-- RELIER LES PROJETS AUX CLIENTS ET AUX ENVIRONNEMENTS
-- ============================================================

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS client_id BIGINT
        REFERENCES clients (id)
        ON DELETE SET NULL;


ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS source_connection_id BIGINT
        REFERENCES integration_connections (id)
        ON DELETE SET NULL;


ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS default_environment_id BIGINT
        REFERENCES deployment_environments (id)
        ON DELETE SET NULL;


CREATE TABLE IF NOT EXISTS project_environments (
    id BIGSERIAL PRIMARY KEY,

    project_id BIGINT NOT NULL,
    environment_id BIGINT NOT NULL,

    is_default BOOLEAN NOT NULL DEFAULT FALSE,

    created_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT project_environments_unique
        UNIQUE (
            project_id,
            environment_id
        ),

    CONSTRAINT project_environments_project_fk
        FOREIGN KEY (project_id)
        REFERENCES projects (id)
        ON DELETE CASCADE,

    CONSTRAINT project_environments_environment_fk
        FOREIGN KEY (environment_id)
        REFERENCES deployment_environments (id)
        ON DELETE CASCADE
);


-- ============================================================
-- RELIER UN DÉPLOIEMENT À SON ENVIRONNEMENT
-- ============================================================

ALTER TABLE deployments
    ADD COLUMN IF NOT EXISTS environment_id BIGINT
        REFERENCES deployment_environments (id)
        ON DELETE SET NULL;


CREATE INDEX IF NOT EXISTS
    deployments_environment_idx
ON deployments (
    environment_id
);


-- ============================================================
-- CLIENT PIXIMIND PAR DÉFAUT
-- ============================================================

INSERT INTO clients (
    name,
    slug,
    status
)
VALUES (
    'Piximind',
    'piximind',
    'active'
)
ON CONFLICT (slug)
DO NOTHING;


-- ============================================================
-- LES ADMINISTRATEURS EXISTANTS DEVIENNENT
-- PROPRIÉTAIRES DU CLIENT PIXIMIND
-- ============================================================

INSERT INTO client_memberships (
    client_id,
    user_id,
    membership_role
)
SELECT
    client.id,
    platform_user.id,
    'owner'

FROM clients AS client

INNER JOIN users AS platform_user
    ON TRUE

INNER JOIN user_roles AS user_role
    ON user_role.user_id =
        platform_user.id

INNER JOIN roles AS role
    ON role.id =
        user_role.role_id

WHERE
    client.slug = 'piximind'
    AND role.code = 'admin'

ON CONFLICT (
    client_id,
    user_id
)
DO NOTHING;


-- ============================================================
-- MISE À JOUR DE updated_at
-- ============================================================

CREATE OR REPLACE FUNCTION
    update_infrastructure_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


DROP TRIGGER IF EXISTS
    clients_updated_at_trigger
ON clients;


CREATE TRIGGER
    clients_updated_at_trigger
BEFORE UPDATE ON clients
FOR EACH ROW
EXECUTE FUNCTION
    update_infrastructure_updated_at();


DROP TRIGGER IF EXISTS
    deployment_environments_updated_at_trigger
ON deployment_environments;


CREATE TRIGGER
    deployment_environments_updated_at_trigger
BEFORE UPDATE ON deployment_environments
FOR EACH ROW
EXECUTE FUNCTION
    update_infrastructure_updated_at();


-- ============================================================
-- ENREGISTREMENT DE LA MIGRATION
-- ============================================================

INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    '006',
    'Clients et environnements de déploiement'
)
ON CONFLICT (version)
DO NOTHING;


COMMIT;