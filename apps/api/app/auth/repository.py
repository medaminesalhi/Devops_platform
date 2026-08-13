from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from app.database import get_database_connection


USER_SELECT = """
    SELECT
        u.id,
        u.username,
        u.email,
        u.password_hash,
        u.first_name,
        u.last_name,
        u.company,
        u.is_active,
        u.status,
        u.last_login_at,
        u.created_at,
        u.updated_at,
        u.approved_at,
        u.approved_by,
        u.rejected_at,
        u.rejection_reason,
        u.suspended_at,

        COALESCE(
            ARRAY(
                SELECT r.code
                FROM user_roles AS ur
                INNER JOIN roles AS r
                    ON r.id = ur.role_id
                WHERE ur.user_id = u.id
                ORDER BY r.code
            ),
            ARRAY[]::VARCHAR[]
        ) AS roles

    FROM users AS u
"""


def find_user_by_identifier(
    identifier: str,
) -> dict[str, Any] | None:
    """
    Cherche un utilisateur avec son username
    ou son adresse email.
    """

    query = f"""
        {USER_SELECT}
        WHERE
            LOWER(u.username) = LOWER(%s)
            OR LOWER(u.email) = LOWER(%s)
        LIMIT 1;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            (
                identifier,
                identifier,
            ),
        ).fetchone()


def find_user_by_id(
    user_id: int,
) -> dict[str, Any] | None:
    query = f"""
        {USER_SELECT}
        WHERE u.id = %s
        LIMIT 1;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            (user_id,),
        ).fetchone()


def update_last_login(
    user_id: int,
) -> None:
    """
    Enregistre la date de la dernière connexion réussie.
    """

    query = """
        UPDATE users
        SET
            last_login_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s;
    """

    with get_database_connection() as connection:
        connection.execute(
            query,
            (user_id,),
        )


def record_login_attempt(
    *,
    user_id: int | None,
    identifier: str,
    success: bool,
    failure_reason: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    with get_database_connection() as connection:
        connection.execute(
            """
                INSERT INTO auth_login_history (
                    user_id,
                    identifier,
                    success,
                    failure_reason,
                    ip_address,
                    user_agent
                )
                VALUES (%s, %s, %s, %s, %s, %s);
            """,
            (
                user_id,
                identifier[:255],
                success,
                failure_reason,
                ip_address[:64] if ip_address else None,
                user_agent[:2000] if user_agent else None,
            ),
        )


def create_auth_session(
    *,
    user_id: int,
    token_hash: str,
    remember_me: bool,
    expires_at: datetime,
) -> None:
    """
    Enregistre une nouvelle session dans PostgreSQL.
    """

    query = """
        INSERT INTO auth_sessions (
            user_id,
            token_hash,
            remember_me,
            expires_at
        )
        VALUES (
            %s,
            %s,
            %s,
            %s
        );
    """

    with get_database_connection() as connection:
        connection.execute(
            query,
            (
                user_id,
                token_hash,
                remember_me,
                expires_at,
            ),
        )


def find_user_by_session(
    token_hash: str,
) -> dict[str, Any] | None:
    """
    Cherche l'utilisateur associé à une session active.

    Une session devient immédiatement inutilisable si le compte
    n'est plus actif ou si son statut n'est plus "active".
    """

    query = """
        SELECT
            session.id AS session_id,
            session.expires_at,

            u.id,
            u.username,
            u.email,
            u.first_name,
            u.last_name,
            u.company,
            u.status,
            u.last_login_at,
            u.created_at,

            COALESCE(
                ARRAY(
                    SELECT r.code
                    FROM user_roles AS ur
                    INNER JOIN roles AS r
                        ON r.id = ur.role_id
                    WHERE ur.user_id = u.id
                    ORDER BY r.code
                ),
                ARRAY[]::VARCHAR[]
            ) AS roles

        FROM auth_sessions AS session

        INNER JOIN users AS u
            ON u.id = session.user_id

        WHERE
            session.token_hash = %s
            AND session.revoked_at IS NULL
            AND session.expires_at > CURRENT_TIMESTAMP
            AND u.is_active = TRUE
            AND u.status = 'active'

        LIMIT 1;
    """

    with get_database_connection() as connection:
        session_user = connection.execute(
            query,
            (token_hash,),
        ).fetchone()

        if session_user is not None:
            connection.execute(
                """
                    UPDATE auth_sessions
                    SET last_used_at = CURRENT_TIMESTAMP
                    WHERE id = %s;
                """,
                (session_user["session_id"],),
            )

        return session_user


def revoke_auth_session(
    session_id: int,
) -> None:
    """
    Révoque une session pendant la déconnexion.
    """

    query = """
        UPDATE auth_sessions
        SET revoked_at = CURRENT_TIMESTAMP
        WHERE
            id = %s
            AND revoked_at IS NULL;
    """

    with get_database_connection() as connection:
        connection.execute(
            query,
            (session_id,),
        )


def revoke_other_auth_sessions(
    *,
    user_id: int,
    current_session_id: int,
) -> int:
    with get_database_connection() as connection:
        result = connection.execute(
            """
                UPDATE auth_sessions
                SET revoked_at = CURRENT_TIMESTAMP
                WHERE user_id = %s
                  AND id <> %s
                  AND revoked_at IS NULL;
            """,
            (user_id, current_session_id),
        )

        return int(result.rowcount or 0)


def revoke_all_user_sessions(
    user_id: int,
) -> int:
    with get_database_connection() as connection:
        result = connection.execute(
            """
                UPDATE auth_sessions
                SET revoked_at = CURRENT_TIMESTAMP
                WHERE user_id = %s
                  AND revoked_at IS NULL;
            """,
            (user_id,),
        )

        return int(result.rowcount or 0)


def create_pending_user(
    *,
    username: str,
    email: str,
    password_hash: str,
    first_name: str | None,
    last_name: str | None,
    company: str | None,
) -> dict[str, Any]:
    """
    Crée un compte issu de l'inscription publique.

    Aucun rôle n'est attribué ici. Le compte reste inutilisable
    jusqu'à son approbation par un administrateur.
    """

    with get_database_connection() as connection:
        existing_user = connection.execute(
            """
                SELECT id
                FROM users
                WHERE
                    LOWER(username) = LOWER(%s)
                    OR LOWER(email) = LOWER(%s)
                LIMIT 1;
            """,
            (username, email),
        ).fetchone()

        if existing_user is not None:
            raise ValueError(
                "Un utilisateur utilise déjà ce nom ou cette adresse email."
            )

        user = connection.execute(
            """
                INSERT INTO users (
                    username,
                    email,
                    password_hash,
                    first_name,
                    last_name,
                    company,
                    is_active,
                    status
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    FALSE,
                    'pending'
                )
                RETURNING
                    id,
                    username,
                    email,
                    first_name,
                    last_name,
                    company,
                    status,
                    created_at;
            """,
            (
                username,
                email,
                password_hash,
                first_name,
                last_name,
                company,
            ),
        ).fetchone()

        if user is None:
            raise RuntimeError(
                "Le compte utilisateur n'a pas pu être créé."
            )

        return user


def create_user_with_role(
    *,
    username: str,
    email: str,
    password_hash: str,
    first_name: str | None,
    last_name: str | None,
    role_code: str,
) -> dict[str, Any]:
    """
    Crée un utilisateur actif puis lui attribue un rôle.

    Cette fonction est utilisée par la commande create-admin et
    ne passe donc pas par le workflow d'approbation publique.
    """

    with get_database_connection() as connection:
        role = connection.execute(
            """
                SELECT id
                FROM roles
                WHERE code = %s
                LIMIT 1;
            """,
            (role_code,),
        ).fetchone()

        if role is None:
            raise ValueError(
                f"Le rôle {role_code} n'existe pas."
            )

        existing_user = connection.execute(
            """
                SELECT id
                FROM users
                WHERE
                    LOWER(username) = LOWER(%s)
                    OR LOWER(email) = LOWER(%s)
                LIMIT 1;
            """,
            (username, email),
        ).fetchone()

        if existing_user is not None:
            raise ValueError(
                "Un utilisateur utilise déjà ce nom ou cette adresse email."
            )

        user = connection.execute(
            """
                INSERT INTO users (
                    username,
                    email,
                    password_hash,
                    first_name,
                    last_name,
                    is_active,
                    status,
                    approved_at
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    TRUE,
                    'active',
                    CURRENT_TIMESTAMP
                )
                RETURNING
                    id,
                    username,
                    email,
                    first_name,
                    last_name,
                    is_active,
                    status,
                    created_at;
            """,
            (
                username,
                email,
                password_hash,
                first_name,
                last_name,
            ),
        ).fetchone()

        if user is None:
            raise RuntimeError(
                "L'utilisateur n'a pas pu être créé."
            )

        connection.execute(
            """
                INSERT INTO user_roles (
                    user_id,
                    role_id
                )
                VALUES (%s, %s);
            """,
            (user["id"], role["id"]),
        )

        return user


def update_user_profile(
    *,
    user_id: int,
    email: str,
    first_name: str | None,
    last_name: str | None,
    company: str | None,
) -> dict[str, Any]:
    with get_database_connection() as connection:
        duplicate = connection.execute(
            """
                SELECT id
                FROM users
                WHERE LOWER(email) = LOWER(%s)
                  AND id <> %s
                LIMIT 1;
            """,
            (email, user_id),
        ).fetchone()

        if duplicate is not None:
            raise ValueError(
                "Cette adresse email est déjà utilisée."
            )

        connection.execute(
            """
                UPDATE users
                SET
                    email = %s,
                    first_name = %s,
                    last_name = %s,
                    company = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s;
            """,
            (
                email,
                first_name,
                last_name,
                company,
                user_id,
            ),
        )

    user = find_user_by_id(user_id)

    if user is None:
        raise RuntimeError(
            "L'utilisateur n'existe plus."
        )

    return user


def get_user_password_hash(
    user_id: int,
) -> str | None:
    with get_database_connection() as connection:
        row = connection.execute(
            """
                SELECT password_hash
                FROM users
                WHERE id = %s
                LIMIT 1;
            """,
            (user_id,),
        ).fetchone()

        return str(row["password_hash"]) if row else None


def update_user_password(
    *,
    user_id: int,
    password_hash: str,
) -> None:
    with get_database_connection() as connection:
        connection.execute(
            """
                UPDATE users
                SET
                    password_hash = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s;
            """,
            (password_hash, user_id),
        )


def create_audit_log(
    *,
    actor_user_id: int | None,
    action: str,
    resource_type: str,
    resource_id: str | int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    with get_database_connection() as connection:
        connection.execute(
            """
                INSERT INTO audit_logs (
                    actor_user_id,
                    action,
                    resource_type,
                    resource_id,
                    metadata
                )
                VALUES (%s, %s, %s, %s, %s);
            """,
            (
                actor_user_id,
                action,
                resource_type,
                str(resource_id) if resource_id is not None else None,
                Jsonb(metadata or {}),
            ),
        )
