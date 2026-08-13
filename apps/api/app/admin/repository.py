from __future__ import annotations

from typing import Any

from app.database import get_database_connection


ROLE_CODES = (
    "admin",
    "devops",
    "developer",
    "viewer",
)


USER_ADMIN_SELECT = """
    SELECT
        u.id,
        u.username,
        u.email,
        u.first_name,
        u.last_name,
        u.company,
        u.status,
        u.is_active,
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
        ) AS roles,

        (
            SELECT COUNT(*)::INTEGER
            FROM deployments AS d
            WHERE d.triggered_by = u.id
        ) AS deployment_count,

        (
            SELECT MAX(d.created_at)
            FROM deployments AS d
            WHERE d.triggered_by = u.id
        ) AS last_deployment_at,

        (
            SELECT COUNT(*)::INTEGER
            FROM auth_login_history AS history
            WHERE history.user_id = u.id
              AND history.success = TRUE
        ) AS successful_login_count

    FROM users AS u
"""


def list_roles() -> list[dict[str, Any]]:
    with get_database_connection() as connection:
        return connection.execute(
            """
                SELECT code, name, description
                FROM roles
                ORDER BY
                    CASE code
                        WHEN 'admin' THEN 1
                        WHEN 'devops' THEN 2
                        WHEN 'developer' THEN 3
                        WHEN 'viewer' THEN 4
                        ELSE 99
                    END,
                    code;
            """
        ).fetchall()


def get_admin_summary() -> dict[str, Any]:
    with get_database_connection() as connection:
        users = connection.execute(
            """
                SELECT
                    COUNT(*)::INTEGER AS total_users,
                    COUNT(*) FILTER (WHERE status = 'pending')::INTEGER AS pending_users,
                    COUNT(*) FILTER (WHERE status = 'active')::INTEGER AS active_users,
                    COUNT(*) FILTER (WHERE status = 'rejected')::INTEGER AS rejected_users,
                    COUNT(*) FILTER (WHERE status = 'suspended')::INTEGER AS suspended_users
                FROM users;
            """
        ).fetchone()

        deployments = connection.execute(
            """
                SELECT
                    COUNT(*)::INTEGER AS total_deployments,
                    COUNT(*) FILTER (
                        WHERE created_at::DATE = CURRENT_DATE
                    )::INTEGER AS deployments_today,
                    COUNT(*) FILTER (WHERE status = 'succeeded')::INTEGER AS succeeded_deployments,
                    COUNT(*) FILTER (WHERE status = 'failed')::INTEGER AS failed_deployments,
                    COUNT(*) FILTER (
                        WHERE status IN ('queued', 'running', 'waiting_confirmation')
                    )::INTEGER AS active_deployments
                FROM deployments;
            """
        ).fetchone()

    return {
        **(users or {}),
        **(deployments or {}),
    }


def list_users(
    *,
    search: str | None = None,
    status: str | None = None,
    role: str | None = None,
) -> list[dict[str, Any]]:
    conditions = ["1 = 1"]
    parameters: list[Any] = []

    if search:
        conditions.append(
            """
                (
                    u.username ILIKE '%%' || %s || '%%'
                    OR u.email ILIKE '%%' || %s || '%%'
                    OR COALESCE(u.first_name, '') ILIKE '%%' || %s || '%%'
                    OR COALESCE(u.last_name, '') ILIKE '%%' || %s || '%%'
                    OR COALESCE(u.company, '') ILIKE '%%' || %s || '%%'
                )
            """
        )
        parameters.extend([search] * 5)

    if status:
        conditions.append("u.status = %s")
        parameters.append(status)

    if role:
        conditions.append(
            """
                EXISTS (
                    SELECT 1
                    FROM user_roles AS filtered_ur
                    INNER JOIN roles AS filtered_role
                        ON filtered_role.id = filtered_ur.role_id
                    WHERE filtered_ur.user_id = u.id
                      AND filtered_role.code = %s
                )
            """
        )
        parameters.append(role)

    query = f"""
        {USER_ADMIN_SELECT}
        WHERE {' AND '.join(conditions)}
        ORDER BY
            CASE u.status
                WHEN 'pending' THEN 1
                WHEN 'active' THEN 2
                WHEN 'suspended' THEN 3
                WHEN 'rejected' THEN 4
                ELSE 9
            END,
            u.created_at DESC,
            u.id DESC
        LIMIT 500;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            tuple(parameters),
        ).fetchall()


def find_user_detail(
    user_id: int,
) -> dict[str, Any] | None:
    query = f"""
        {USER_ADMIN_SELECT}
        WHERE u.id = %s
        LIMIT 1;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            (user_id,),
        ).fetchone()


def list_user_logins(
    user_id: int,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    with get_database_connection() as connection:
        return connection.execute(
            """
                SELECT
                    id,
                    success,
                    failure_reason,
                    ip_address,
                    user_agent,
                    logged_at
                FROM auth_login_history
                WHERE user_id = %s
                ORDER BY logged_at DESC, id DESC
                LIMIT %s;
            """,
            (user_id, limit),
        ).fetchall()


def list_user_deployments(
    user_id: int,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    with get_database_connection() as connection:
        return connection.execute(
            """
                SELECT
                    d.id,
                    d.project_id,
                    project.name AS project_name,
                    d.environment_id,
                    environment.name AS environment_name,
                    environment.code AS environment_code,
                    d.version,
                    d.status,
                    d.progress,
                    d.current_stage,
                    d.current_stage_label,
                    d.created_at,
                    d.started_at,
                    d.finished_at,
                    d.error_code,
                    d.error_message
                FROM deployments AS d
                INNER JOIN projects AS project
                    ON project.id = d.project_id
                LEFT JOIN deployment_environments AS environment
                    ON environment.id = d.environment_id
                WHERE d.triggered_by = %s
                ORDER BY d.created_at DESC, d.id DESC
                LIMIT %s;
            """,
            (user_id, limit),
        ).fetchall()


def role_exists(
    role_code: str,
) -> bool:
    with get_database_connection() as connection:
        row = connection.execute(
            """
                SELECT 1 AS found
                FROM roles
                WHERE code = %s
                LIMIT 1;
            """,
            (role_code,),
        ).fetchone()

        return bool(row)


def approve_user(
    *,
    user_id: int,
    role_code: str,
    approved_by: int,
) -> bool:
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
            raise ValueError("Le rôle demandé n'existe pas.")

        result = connection.execute(
            """
                UPDATE users
                SET
                    status = 'active',
                    is_active = TRUE,
                    approved_at = CURRENT_TIMESTAMP,
                    approved_by = %s,
                    rejected_at = NULL,
                    rejection_reason = NULL,
                    suspended_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND status IN ('pending', 'rejected');
            """,
            (approved_by, user_id),
        )

        if not result.rowcount:
            return False

        connection.execute(
            "DELETE FROM user_roles WHERE user_id = %s;",
            (user_id,),
        )
        connection.execute(
            """
                INSERT INTO user_roles (user_id, role_id)
                VALUES (%s, %s);
            """,
            (user_id, role["id"]),
        )

        return True


def reject_user(
    *,
    user_id: int,
    reason: str | None,
) -> bool:
    with get_database_connection() as connection:
        result = connection.execute(
            """
                UPDATE users
                SET
                    status = 'rejected',
                    is_active = FALSE,
                    rejected_at = CURRENT_TIMESTAMP,
                    rejection_reason = %s,
                    suspended_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND status = 'pending';
            """,
            (reason, user_id),
        )

        connection.execute(
            "DELETE FROM user_roles WHERE user_id = %s;",
            (user_id,),
        )
        connection.execute(
            """
                UPDATE auth_sessions
                SET revoked_at = CURRENT_TIMESTAMP
                WHERE user_id = %s
                  AND revoked_at IS NULL;
            """,
            (user_id,),
        )

        return bool(result.rowcount)


def suspend_user(
    *,
    user_id: int,
) -> bool:
    with get_database_connection() as connection:
        result = connection.execute(
            """
                UPDATE users
                SET
                    status = 'suspended',
                    is_active = FALSE,
                    suspended_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND status = 'active';
            """,
            (user_id,),
        )

        connection.execute(
            """
                UPDATE auth_sessions
                SET revoked_at = CURRENT_TIMESTAMP
                WHERE user_id = %s
                  AND revoked_at IS NULL;
            """,
            (user_id,),
        )

        return bool(result.rowcount)


def activate_user(
    *,
    user_id: int,
    approved_by: int,
) -> bool:
    with get_database_connection() as connection:
        result = connection.execute(
            """
                UPDATE users
                SET
                    status = 'active',
                    is_active = TRUE,
                    approved_at = COALESCE(approved_at, CURRENT_TIMESTAMP),
                    approved_by = COALESCE(approved_by, %s),
                    rejected_at = NULL,
                    rejection_reason = NULL,
                    suspended_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND status = 'suspended';
            """,
            (approved_by, user_id),
        )

        return bool(result.rowcount)


def set_user_role(
    *,
    user_id: int,
    role_code: str,
) -> bool:
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
            raise ValueError("Le rôle demandé n'existe pas.")

        user = connection.execute(
            "SELECT id FROM users WHERE id = %s AND status = 'active' LIMIT 1;",
            (user_id,),
        ).fetchone()

        if user is None:
            return False

        connection.execute(
            "DELETE FROM user_roles WHERE user_id = %s;",
            (user_id,),
        )
        connection.execute(
            """
                INSERT INTO user_roles (user_id, role_id)
                VALUES (%s, %s);
            """,
            (user_id, role["id"]),
        )

        return True
