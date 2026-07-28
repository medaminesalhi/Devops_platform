BEGIN;

-- ============================================================
-- TABLE DES MIGRATIONS
-- ============================================================

CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(50) PRIMARY KEY,
    description VARCHAR(255) NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- TABLE DES RÔLES
-- ============================================================

CREATE TABLE IF NOT EXISTS roles (
    id SMALLSERIAL PRIMARY KEY,
    code VARCHAR(50) NOT NULL UNIQUE,
    name VARCHAR(100) NOT NULL,
    description VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- TABLE DES UTILISATEURS DE LA PLATEFORME
-- ============================================================

CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,

    username VARCHAR(60) NOT NULL,
    email VARCHAR(255) NOT NULL,
    password_hash TEXT NOT NULL,

    first_name VARCHAR(100),
    last_name VARCHAR(100),

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    last_login_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT users_username_format_check
        CHECK (
            username ~ '^[A-Za-z0-9._-]{3,60}$'
        ),

    CONSTRAINT users_email_format_check
        CHECK (
            POSITION('@' IN email) > 1
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS
    users_username_lower_unique_idx
ON users (LOWER(username));

CREATE UNIQUE INDEX IF NOT EXISTS
    users_email_lower_unique_idx
ON users (LOWER(email));

-- ============================================================
-- ASSOCIATION UTILISATEURS / RÔLES
-- ============================================================

CREATE TABLE IF NOT EXISTS user_roles (
    user_id BIGINT NOT NULL,
    role_id SMALLINT NOT NULL,

    assigned_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (user_id, role_id),

    CONSTRAINT user_roles_user_fk
        FOREIGN KEY (user_id)
        REFERENCES users (id)
        ON DELETE CASCADE,

    CONSTRAINT user_roles_role_fk
        FOREIGN KEY (role_id)
        REFERENCES roles (id)
        ON DELETE CASCADE
);

-- ============================================================
-- TABLE DES SESSIONS DE CONNEXION
-- ============================================================

CREATE TABLE IF NOT EXISTS auth_sessions (
    id BIGSERIAL PRIMARY KEY,

    user_id BIGINT NOT NULL,

    token_hash CHAR(64) NOT NULL UNIQUE,

    remember_me BOOLEAN NOT NULL DEFAULT FALSE,

    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT auth_sessions_user_fk
        FOREIGN KEY (user_id)
        REFERENCES users (id)
        ON DELETE CASCADE,

    CONSTRAINT auth_sessions_expiry_check
        CHECK (
            expires_at > created_at
        )
);

CREATE INDEX IF NOT EXISTS
    auth_sessions_user_idx
ON auth_sessions (user_id);

CREATE INDEX IF NOT EXISTS
    auth_sessions_active_idx
ON auth_sessions (token_hash, expires_at)
WHERE revoked_at IS NULL;

-- ============================================================
-- RÔLES INITIAUX
-- ============================================================

INSERT INTO roles (
    code,
    name,
    description
)
VALUES
    (
        'admin',
        'Administrateur',
        'Accès complet à la plateforme'
    ),
    (
        'devops',
        'DevOps',
        'Gestion des projets et des déploiements'
    ),
    (
        'developer',
        'Développeur',
        'Consultation et lancement des déploiements'
    ),
    (
        'viewer',
        'Lecteur',
        'Consultation sans modification'
    )
ON CONFLICT (code)
DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description;

-- ============================================================
-- ENREGISTRER LA MIGRATION
-- ============================================================

INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    '001',
    'Création des tables de connexion'
)
ON CONFLICT (version)
DO NOTHING;

COMMIT;