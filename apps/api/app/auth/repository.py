from __future__ import annotations

from datetime import datetime
from typing import Any

from app.database import get_database_connection


def find_user_by_identifier(
    identifier: str,
) -> dict[str, Any] | None:
    """
    Cherche un utilisateur avec son username
    ou son adresse email.
    """

    query = """
        SELECT
            u.id,
            u.username,
            u.email,
            u.password_hash,
            u.first_name,
            u.last_name,
            u.is_active,
            u.last_login_at,

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

    La session doit :
    - exister ;
    - ne pas être révoquée ;
    - ne pas être expirée ;
    - appartenir à un utilisateur actif.
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
    Crée un utilisateur puis lui attribue un rôle.

    Cette fonction sera utilisée par la commande
    create-admin.
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
            (
                username,
                email,
            ),
        ).fetchone()

        if existing_user is not None:
            raise ValueError(
                "Un utilisateur utilise déjà ce "
                "nom ou cette adresse email."
            )

        user = connection.execute(
            """
                INSERT INTO users (
                    username,
                    email,
                    password_hash,
                    first_name,
                    last_name
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                RETURNING
                    id,
                    username,
                    email,
                    first_name,
                    last_name,
                    is_active,
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
                VALUES (
                    %s,
                    %s
                );
            """,
            (
                user["id"],
                role["id"],
            ),
        )

        return user