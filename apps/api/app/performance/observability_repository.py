from __future__ import annotations

from typing import Any

from app.database import get_database_connection


STACK_SELECT = """
    SELECT
        stack.*,
        project.name AS project_name,
        connection.name AS kubernetes_connection_name,
        connection.base_url AS kubernetes_base_url,
        connection.verify_ssl AS kubernetes_verify_ssl,
        connection.enabled AS kubernetes_enabled,
        connection.status AS kubernetes_status,
        credential.auth_type AS kubernetes_auth_type,
        credential.secret_ciphertext AS kubernetes_secret_ciphertext
    FROM performance_observability_stacks AS stack
    INNER JOIN projects AS project
        ON project.id = stack.project_id
    INNER JOIN integration_connections AS connection
        ON connection.id = stack.kubernetes_connection_id
    LEFT JOIN integration_credentials AS credential
        ON credential.connection_id = connection.id
"""


def create_stack(
    *,
    project_id: int,
    created_by: int,
    kubernetes_connection_id: int,
    namespace: str,
    retention_days: int,
    prometheus_storage_size: str,
    grafana_storage_size: str,
    storage_class_name: str | None,
    ingress_enabled: bool,
    ingress_class_name: str | None,
    grafana_host: str | None,
    grafana_tls_enabled: bool,
    grafana_tls_secret_name: str | None,
    grafana_admin_password_ciphertext: str,
) -> dict[str, Any]:
    with get_database_connection() as connection:
        row = connection.execute(
            """
                INSERT INTO performance_observability_stacks (
                    project_id,
                    created_by,
                    kubernetes_connection_id,
                    namespace,
                    retention_days,
                    prometheus_storage_size,
                    grafana_storage_size,
                    storage_class_name,
                    ingress_enabled,
                    ingress_class_name,
                    grafana_host,
                    grafana_tls_enabled,
                    grafana_tls_secret_name,
                    grafana_admin_password_ciphertext,
                    status
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, 'queued'
                )
                RETURNING id;
            """,
            (
                project_id,
                created_by,
                kubernetes_connection_id,
                namespace,
                retention_days,
                prometheus_storage_size,
                grafana_storage_size,
                storage_class_name,
                ingress_enabled,
                ingress_class_name,
                grafana_host,
                grafana_tls_enabled,
                grafana_tls_secret_name,
                grafana_admin_password_ciphertext,
            ),
        ).fetchone()

        if row is None:
            raise RuntimeError("Impossible de créer la stack d'observabilité.")

        stack_id = int(row["id"])
        connection.execute(
            """
                INSERT INTO performance_observability_logs (
                    stack_id,
                    level,
                    message
                )
                VALUES (%s, 'info', %s);
            """,
            (
                stack_id,
                "Demande de provisioning Grafana + Prometheus ajoutée à la file.",
            ),
        )

    result = find_stack(stack_id)
    if result is None:
        raise RuntimeError("La stack créée est introuvable.")
    return result


def find_stack(
    stack_id: int,
    *,
    owner_user_id: int | None = None,
) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        return connection.execute(
            f"""
                {STACK_SELECT}
                WHERE stack.id = %s
                  AND (
                    %s::BIGINT IS NULL
                    OR project.created_by = %s
                  )
                LIMIT 1;
            """,
            (stack_id, owner_user_id, owner_user_id),
        ).fetchone()


def find_ready_stack(
    stack_id: int,
    *,
    owner_user_id: int | None = None,
) -> dict[str, Any] | None:
    row = find_stack(stack_id, owner_user_id=owner_user_id)
    if row is None or row.get("status") != "ready":
        return None
    return row


def list_stacks(
    *,
    owner_user_id: int | None = None,
    project_id: int | None = None,
) -> list[dict[str, Any]]:
    conditions = [
        "stack.status <> 'deleted'",
        "(%s::BIGINT IS NULL OR project.created_by = %s)",
        "(%s::BIGINT IS NULL OR stack.project_id = %s)",
    ]
    parameters = [
        owner_user_id,
        owner_user_id,
        project_id,
        project_id,
    ]

    with get_database_connection() as connection:
        return connection.execute(
            f"""
                {STACK_SELECT}
                WHERE {' AND '.join(conditions)}
                ORDER BY stack.created_at DESC;
            """,
            tuple(parameters),
        ).fetchall()


def list_logs(stack_id: int) -> list[dict[str, Any]]:
    with get_database_connection() as connection:
        return connection.execute(
            """
                SELECT id, stack_id, level, message, created_at
                FROM performance_observability_logs
                WHERE stack_id = %s
                ORDER BY id ASC;
            """,
            (stack_id,),
        ).fetchall()


def add_log(stack_id: int, *, level: str, message: str) -> None:
    with get_database_connection() as connection:
        connection.execute(
            """
                INSERT INTO performance_observability_logs (
                    stack_id,
                    level,
                    message
                )
                VALUES (%s, %s, %s);
            """,
            (stack_id, level, message[:4000]),
        )


def claim_next_stack(worker_name: str) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        row = connection.execute(
            """
                SELECT id
                FROM performance_observability_stacks
                WHERE status = 'queued'
                ORDER BY created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1;
            """
        ).fetchone()

        if row is None:
            return None

        stack_id = int(row["id"])
        connection.execute(
            """
                UPDATE performance_observability_stacks
                SET status = 'provisioning',
                    worker_name = %s,
                    locked_at = NOW(),
                    heartbeat_at = NOW(),
                    started_at = COALESCE(started_at, NOW()),
                    finished_at = NULL,
                    error_code = NULL,
                    error_message = NULL
                WHERE id = %s;
            """,
            (worker_name, stack_id),
        )

    return find_stack(stack_id)


def heartbeat(stack_id: int, worker_name: str) -> None:
    with get_database_connection() as connection:
        connection.execute(
            """
                UPDATE performance_observability_stacks
                SET heartbeat_at = NOW()
                WHERE id = %s
                  AND worker_name = %s
                  AND status = 'provisioning';
            """,
            (stack_id, worker_name),
        )


def finish_ready(
    stack_id: int,
    *,
    prometheus_remote_write_url: str,
    prometheus_query_url: str,
    grafana_base_url: str | None,
) -> None:
    with get_database_connection() as connection:
        connection.execute(
            """
                UPDATE performance_observability_stacks
                SET status = 'ready',
                    prometheus_remote_write_url = %s,
                    prometheus_query_url = %s,
                    grafana_base_url = %s,
                    finished_at = NOW(),
                    heartbeat_at = NOW(),
                    error_code = NULL,
                    error_message = NULL
                WHERE id = %s;
            """,
            (
                prometheus_remote_write_url,
                prometheus_query_url,
                grafana_base_url,
                stack_id,
            ),
        )


def finish_failed(stack_id: int, *, code: str, message: str) -> None:
    with get_database_connection() as connection:
        connection.execute(
            """
                UPDATE performance_observability_stacks
                SET status = 'failed',
                    finished_at = NOW(),
                    heartbeat_at = NOW(),
                    error_code = %s,
                    error_message = %s
                WHERE id = %s;
            """,
            (code, message[:4000], stack_id),
        )


def requeue_stack(stack_id: int) -> bool:
    with get_database_connection() as connection:
        row = connection.execute(
            """
                UPDATE performance_observability_stacks
                SET status = 'queued',
                    worker_name = NULL,
                    locked_at = NULL,
                    heartbeat_at = NULL,
                    started_at = NULL,
                    finished_at = NULL,
                    error_code = NULL,
                    error_message = NULL,
                    prometheus_remote_write_url = NULL,
                    prometheus_query_url = NULL,
                    grafana_base_url = NULL
                WHERE id = %s
                  AND status = 'failed'
                RETURNING id;
            """,
            (stack_id,),
        ).fetchone()
    return row is not None


def fail_stale_stacks(stale_seconds: int) -> int:
    with get_database_connection() as connection:
        rows = connection.execute(
            """
                UPDATE performance_observability_stacks
                SET status = 'failed',
                    finished_at = NOW(),
                    error_code = 'OBSERVABILITY_WORKER_HEARTBEAT_LOST',
                    error_message = 'Le worker observability ne répond plus.'
                WHERE status = 'provisioning'
                  AND heartbeat_at IS NOT NULL
                  AND heartbeat_at < NOW() - (%s * INTERVAL '1 second')
                RETURNING id;
            """,
            (stale_seconds,),
        ).fetchall()
    return len(rows)
