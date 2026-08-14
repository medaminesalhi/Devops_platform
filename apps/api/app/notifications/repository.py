from __future__ import annotations

from typing import Any

from app.database import get_database_connection


NOTIFICATION_SELECT = """
    SELECT
        notification.id,
        notification.user_id,
        notification.connection_id,
        notification.project_id,
        notification.deployment_id,
        notification.environment_id,
        notification.notification_type,
        notification.severity,
        notification.title,
        notification.message,
        notification.resource_type,
        notification.resource_id,
        notification.action_url,
        notification.metadata,
        notification.read_at,
        notification.resolved_at,
        notification.created_at
    FROM notifications AS notification
"""


def list_notifications(
    *,
    user_id: int,
    limit: int = 20,
    unread_only: bool = False,
) -> list[dict[str, Any]]:
    conditions = [
        "notification.user_id = %s",
    ]
    parameters: list[Any] = [
        user_id,
    ]

    if unread_only:
        conditions.append(
            "notification.read_at IS NULL"
        )

    parameters.append(limit)

    with get_database_connection() as connection:
        return connection.execute(
            f"""
                {NOTIFICATION_SELECT}
                WHERE {' AND '.join(conditions)}
                ORDER BY
                    CASE
                        WHEN notification.read_at IS NULL THEN 0
                        ELSE 1
                    END,
                    notification.created_at DESC,
                    notification.id DESC
                LIMIT %s;
            """,
            tuple(parameters),
        ).fetchall()


def count_unread_notifications(
    *,
    user_id: int,
) -> int:
    with get_database_connection() as connection:
        row = connection.execute(
            """
                SELECT COUNT(*)::INTEGER AS count
                FROM notifications
                WHERE user_id = %s
                  AND read_at IS NULL;
            """,
            (user_id,),
        ).fetchone()

    return int(row["count"] if row else 0)


def mark_notification_read(
    *,
    notification_id: int,
    user_id: int,
) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        row = connection.execute(
            """
                UPDATE notifications
                SET read_at = COALESCE(
                    read_at,
                    CURRENT_TIMESTAMP
                )
                WHERE id = %s
                  AND user_id = %s
                RETURNING id;
            """,
            (
                notification_id,
                user_id,
            ),
        ).fetchone()

        if row is None:
            return None

        return connection.execute(
            f"""
                {NOTIFICATION_SELECT}
                WHERE notification.id = %s
                  AND notification.user_id = %s
                LIMIT 1;
            """,
            (
                notification_id,
                user_id,
            ),
        ).fetchone()


def mark_all_notifications_read(
    *,
    user_id: int,
) -> int:
    with get_database_connection() as connection:
        rows = connection.execute(
            """
                UPDATE notifications
                SET read_at = CURRENT_TIMESTAMP
                WHERE user_id = %s
                  AND read_at IS NULL
                RETURNING id;
            """,
            (user_id,),
        ).fetchall()

    return len(rows)
