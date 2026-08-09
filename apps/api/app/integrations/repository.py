from __future__ import annotations

import json

from typing import Any

from app.database import (
    get_database_connection,
)


CONNECTION_SELECT = """
    SELECT
        connection.id,
        connection.name,
        connection.provider_type,
        connection.base_url,
        connection.registry_url,
        connection.registry_repository,
        connection.environment,
        connection.description,
        connection.enabled,
        connection.verify_ssl,
        connection.monitoring_enabled,
        connection.check_interval_seconds,
        connection.failure_threshold,
        connection.status,
        connection.consecutive_failures,
        connection.last_http_status,
        connection.last_error,
        connection.last_checked_at,
        connection.last_latency_ms,
        connection.created_by,
        connection.created_at,
        connection.updated_at,

        COALESCE(
            credential.auth_type,
            'none'
        ) AS auth_type,

        credential.username,
        credential.secret_ciphertext,

        (
            credential.secret_ciphertext
            IS NOT NULL
        ) AS credential_configured

    FROM integration_connections
        AS connection

    LEFT JOIN integration_credentials
        AS credential
        ON credential.connection_id
           = connection.id
"""


def _fetch_connection(
    database_connection,
    connection_id: int,
) -> dict[str, Any] | None:
    return database_connection.execute(
        f"""
            {CONNECTION_SELECT}

            WHERE connection.id = %s

            LIMIT 1;
        """,
        (
            connection_id,
        ),
    ).fetchone()


def list_connections(
) -> list[dict[str, Any]]:
    with get_database_connection() as connection:
        return connection.execute(
            f"""
                {CONNECTION_SELECT}

                ORDER BY connection.name ASC;
            """
        ).fetchall()


def find_connection(
    connection_id: int,
) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        return _fetch_connection(
            connection,
            connection_id,
        )


def create_connection(
    *,
    name: str,
    provider_type: str,
    base_url: str,
    registry_url: str | None,
    registry_repository: str | None,
    environment: str,
    description: str | None,
    verify_ssl: bool,
    auth_type: str,
    username: str | None,
    secret_ciphertext: str | None,
    monitoring_enabled: bool,
    check_interval_seconds: int,
    failure_threshold: int,
    user_id: int,
) -> dict[str, Any]:
    with get_database_connection() as connection:
        created_row = connection.execute(
            """
                INSERT INTO
                    integration_connections (
                        name,
                        provider_type,
                        base_url,
                        registry_url,
                        registry_repository,
                        environment,
                        description,
                        verify_ssl,
                        monitoring_enabled,
                        check_interval_seconds,
                        failure_threshold,
                        created_by
                    )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                RETURNING id;
            """,
            (
                name,
                provider_type,
                base_url,
                registry_url,
                registry_repository,
                environment,
                description,
                verify_ssl,
                monitoring_enabled,
                check_interval_seconds,
                failure_threshold,
                user_id,
            ),
        ).fetchone()

        if created_row is None:
            raise RuntimeError(
                "La connexion n'a pas "
                "pu être créée."
            )

        connection_id = int(
            created_row["id"]
        )

        connection.execute(
            """
                INSERT INTO
                    integration_credentials (
                        connection_id,
                        auth_type,
                        username,
                        secret_ciphertext
                    )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s
                );
            """,
            (
                connection_id,
                auth_type,
                username,
                secret_ciphertext,
            ),
        )

        connection.execute(
            """
                INSERT INTO
                    integration_activity_logs (
                        connection_id,
                        user_id,
                        action,
                        details
                    )
                VALUES (
                    %s,
                    %s,
                    'connection.created',
                    %s::JSONB
                );
            """,
            (
                connection_id,
                user_id,
                json.dumps(
                    {
                        "name": name,
                        "providerType":
                            provider_type,
                        "verifySsl":
                            verify_ssl,
                    }
                ),
            ),
        )

        result = _fetch_connection(
            connection,
            connection_id,
        )

        if result is None:
            raise RuntimeError(
                "La connexion créée "
                "est introuvable."
            )

        return result


def update_connection(
    *,
    connection_id: int,
    name: str,
    provider_type: str,
    base_url: str,
    registry_url: str | None,
    registry_repository: str | None,
    environment: str,
    description: str | None,
    verify_ssl: bool,
    auth_type: str,
    username: str | None,
    replace_secret: bool,
    secret_ciphertext: str | None,
    monitoring_enabled: bool,
    check_interval_seconds: int,
    failure_threshold: int,
    user_id: int,
) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        updated_row = connection.execute(
            """
                UPDATE integration_connections

                SET
                    name = %s,
                    provider_type = %s,
                    base_url = %s,
                    registry_url = %s,
                    registry_repository = %s,
                    environment = %s,
                    description = %s,
                    verify_ssl = %s,
                    monitoring_enabled = %s,
                    check_interval_seconds = %s,
                    failure_threshold = %s,

                    status = 'unchecked',
                    consecutive_failures = 0,
                    last_http_status = NULL,
                    last_error = NULL,
                    last_checked_at = NULL,
                    last_latency_ms = NULL

                WHERE id = %s

                RETURNING id;
            """,
            (
                name,
                provider_type,
                base_url,
                registry_url,
                registry_repository,
                environment,
                description,
                verify_ssl,
                monitoring_enabled,
                check_interval_seconds,
                failure_threshold,
                connection_id,
            ),
        ).fetchone()

        if updated_row is None:
            return None

        if replace_secret:
            connection.execute(
                """
                    INSERT INTO
                        integration_credentials (
                            connection_id,
                            auth_type,
                            username,
                            secret_ciphertext
                        )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s
                    )

                    ON CONFLICT (
                        connection_id
                    )

                    DO UPDATE SET
                        auth_type =
                            EXCLUDED.auth_type,

                        username =
                            EXCLUDED.username,

                        secret_ciphertext =
                            EXCLUDED.secret_ciphertext;
                """,
                (
                    connection_id,
                    auth_type,
                    username,
                    secret_ciphertext,
                ),
            )

        else:
            connection.execute(
                """
                    INSERT INTO
                        integration_credentials (
                            connection_id,
                            auth_type,
                            username,
                            secret_ciphertext
                        )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        NULL
                    )

                    ON CONFLICT (
                        connection_id
                    )

                    DO UPDATE SET
                        auth_type =
                            EXCLUDED.auth_type,

                        username =
                            EXCLUDED.username;
                """,
                (
                    connection_id,
                    auth_type,
                    username,
                ),
            )

        connection.execute(
            """
                INSERT INTO
                    integration_activity_logs (
                        connection_id,
                        user_id,
                        action,
                        details
                    )
                VALUES (
                    %s,
                    %s,
                    'connection.updated',
                    %s::JSONB
                );
            """,
            (
                connection_id,
                user_id,
                json.dumps(
                    {
                        "name": name,
                        "providerType":
                            provider_type,
                        "verifySsl":
                            verify_ssl,
                        "credentialReplaced":
                            replace_secret,
                    }
                ),
            ),
        )

        return _fetch_connection(
            connection,
            connection_id,
        )


def delete_connection(
    *,
    connection_id: int,
) -> dict[str, Any]:
    with get_database_connection() as connection:
        existing = _fetch_connection(
            connection,
            connection_id,
        )

        if existing is None:
            return {
                "deleted": False,
                "reason": "not_found",
                "usageCount": 0,
                "name": None,
            }

        usage_row = connection.execute(
            """
                SELECT
                    COUNT(*)::INTEGER AS total

                FROM environment_connections

                WHERE connection_id = %s;
            """,
            (
                connection_id,
            ),
        ).fetchone()

        usage_count = int(
            usage_row["total"]
            if usage_row
            else 0
        )

        if usage_count > 0:
            return {
                "deleted": False,
                "reason": "in_use",
                "usageCount": usage_count,
                "name": existing["name"],
            }

        connection.execute(
            """
                DELETE FROM
                    integration_connections

                WHERE id = %s;
            """,
            (
                connection_id,
            ),
        )

        return {
            "deleted": True,
            "reason": None,
            "usageCount": 0,
            "name": existing["name"],
        }


def list_due_connection_ids(
) -> list[int]:
    with get_database_connection() as connection:
        rows = connection.execute(
            """
                SELECT id

                FROM integration_connections

                WHERE
                    enabled = TRUE

                    AND monitoring_enabled
                        = TRUE

                    AND (
                        last_checked_at IS NULL

                        OR last_checked_at
                           + (
                               check_interval_seconds
                               * INTERVAL '1 second'
                           )
                           <= CURRENT_TIMESTAMP
                    )

                ORDER BY
                    last_checked_at NULLS FIRST;
            """
        ).fetchall()

    return [
        int(row["id"])
        for row in rows
    ]


def save_health_result(
    *,
    connection_id: int,
    final_status: str,
    consecutive_failures: int,
    result: dict[str, Any],
) -> dict[str, Any]:
    with get_database_connection() as connection:
        previous_connection = (
            _fetch_connection(
                connection,
                connection_id,
            )
        )

        if previous_connection is None:
            raise ValueError(
                "Connexion introuvable."
            )

        previous_status = (
            previous_connection["status"]
        )

        connection.execute(
            """
                INSERT INTO
                    integration_health_checks (
                        connection_id,
                        status,
                        http_status,
                        latency_ms,
                        server_reachable,
                        authenticated,
                        checked_url,
                        message
                    )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                );
            """,
            (
                connection_id,
                final_status,
                result["http_status"],
                result["latency_ms"],
                result["server_reachable"],
                result["authenticated"],
                result["checked_url"],
                result["message"],
            ),
        )

        connection.execute(
            """
                UPDATE integration_connections

                SET
                    status = %s,
                    consecutive_failures = %s,
                    last_http_status = %s,
                    last_error = %s,
                    last_checked_at =
                        CURRENT_TIMESTAMP,
                    last_latency_ms = %s

                WHERE id = %s;
            """,
            (
                final_status,
                consecutive_failures,
                result["http_status"],
                (
                    None
                    if final_status == "online"
                    else result["message"]
                ),
                result["latency_ms"],
                connection_id,
            ),
        )

        connection_name = (
            previous_connection["name"]
        )

        if (
            final_status == "offline"
            and previous_status != "offline"
        ):
            connection.execute(
                """
                    INSERT INTO notifications (
                        connection_id,
                        notification_type,
                        severity,
                        title,
                        message
                    )
                    VALUES (
                        %s,
                        'integration.offline',
                        'critical',
                        %s,
                        %s
                    );
                """,
                (
                    connection_id,
                    (
                        f"{connection_name} "
                        "est inaccessible"
                    ),
                    result["message"],
                ),
            )

        elif (
            final_status == "online"
            and previous_status == "offline"
        ):
            connection.execute(
                """
                    INSERT INTO notifications (
                        connection_id,
                        notification_type,
                        severity,
                        title,
                        message
                    )
                    VALUES (
                        %s,
                        'integration.recovered',
                        'success',
                        %s,
                        %s
                    );
                """,
                (
                    connection_id,
                    (
                        f"{connection_name} "
                        "est rétabli"
                    ),
                    (
                        "Le service répond "
                        "de nouveau correctement."
                    ),
                ),
            )

        updated_connection = (
            _fetch_connection(
                connection,
                connection_id,
            )
        )

        if updated_connection is None:
            raise RuntimeError(
                "Impossible de relire "
                "la connexion."
            )

        return updated_connection