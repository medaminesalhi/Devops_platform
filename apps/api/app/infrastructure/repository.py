from __future__ import annotations

from typing import Any

from app.database import get_database_connection


ROLE_PROVIDER_MAP = {
    "kubernetes": "kubernetes",
    "argocd": "argocd",
    "container_registry": "nexus",
    "gitops_repository": "gitlab",
    "ai_provider": "ollama",
}


REQUIRED_SERVICE_ROLES = {
    "kubernetes",
    "argocd",
    "container_registry",
    "gitops_repository",
}


ENVIRONMENT_SELECT = """
    SELECT
        environment.id,
        environment.client_id,
        environment.name,
        environment.code,
        environment.environment_type,
        environment.description,
        environment.namespace,
        environment.domain,
        environment.configuration_status,
        environment.is_default,
        environment.created_at,
        environment.updated_at,

        client.name AS client_name,
        client.slug AS client_slug,

        (
            SELECT COUNT(*)::INTEGER
            FROM environment_connections
                AS service_link

            WHERE service_link.environment_id =
                environment.id
        ) AS service_total,

        (
            SELECT COUNT(*)::INTEGER

            FROM environment_connections
                AS service_link

            INNER JOIN integration_connections
                AS integration
                ON integration.id =
                    service_link.connection_id

            WHERE
                service_link.environment_id =
                    environment.id

                AND integration.status = 'online'
        ) AS service_online,

        (
            SELECT COUNT(*)::INTEGER

            FROM project_environments
                AS project_environment

            WHERE
                project_environment.environment_id =
                    environment.id
        ) AS project_count,

        (
            SELECT integration.name

            FROM environment_connections
                AS service_link

            INNER JOIN integration_connections
                AS integration
                ON integration.id =
                    service_link.connection_id

            WHERE
                service_link.environment_id =
                    environment.id

                AND service_link.service_role =
                    'kubernetes'

            LIMIT 1
        ) AS kubernetes_connection_name,

        (
            SELECT MAX(
                integration.last_checked_at
            )

            FROM environment_connections
                AS service_link

            INNER JOIN integration_connections
                AS integration
                ON integration.id =
                    service_link.connection_id

            WHERE service_link.environment_id =
                environment.id
        ) AS last_checked_at,

        CASE
            WHEN environment.configuration_status =
                'archived'
            THEN 'archived'

            WHEN (
                SELECT COUNT(*)

                FROM environment_connections
                    AS required_link

                WHERE
                    required_link.environment_id =
                        environment.id

                    AND required_link.service_role IN (
                        'kubernetes',
                        'argocd',
                        'container_registry',
                        'gitops_repository'
                    )
            ) < 4
            THEN 'draft'

            WHEN EXISTS (
                SELECT 1

                FROM environment_connections
                    AS required_link

                INNER JOIN integration_connections
                    AS integration
                    ON integration.id =
                        required_link.connection_id

                WHERE
                    required_link.environment_id =
                        environment.id

                    AND required_link.is_required = TRUE

                    AND integration.status = 'offline'
            )
            THEN 'offline'

            WHEN EXISTS (
                SELECT 1

                FROM environment_connections
                    AS required_link

                INNER JOIN integration_connections
                    AS integration
                    ON integration.id =
                        required_link.connection_id

                WHERE
                    required_link.environment_id =
                        environment.id

                    AND required_link.is_required = TRUE

                    AND integration.status IN (
                        'degraded',
                        'unchecked',
                        'not_configured'
                    )
            )
            THEN 'degraded'

            ELSE 'ready'
        END AS effective_status,

        COALESCE(
            (
                SELECT JSONB_AGG(
                    JSONB_BUILD_OBJECT(
                        'role',
                            service_link.service_role,

                        'required',
                            service_link.is_required,

                        'connectionId',
                            integration.id,

                        'connectionName',
                            integration.name,

                        'providerType',
                            integration.provider_type,

                        'status',
                            integration.status,

                        'lastCheckedAt',
                            integration.last_checked_at,

                        'lastLatencyMs',
                            integration.last_latency_ms
                    )
                    ORDER BY
                        service_link.service_role
                )

                FROM environment_connections
                    AS service_link

                INNER JOIN integration_connections
                    AS integration
                    ON integration.id =
                        service_link.connection_id

                WHERE service_link.environment_id =
                    environment.id
            ),
            '[]'::JSONB
        ) AS services

    FROM deployment_environments
        AS environment

    INNER JOIN clients AS client
        ON client.id =
            environment.client_id
"""


def list_visible_clients(
    *,
    user_id: int,
    is_global_admin: bool,
) -> list[dict[str, Any]]:
    query = """
        SELECT
            client.id,
            client.name,
            client.slug,
            client.status

        FROM clients AS client

        WHERE
            client.status <> 'archived'

            AND (
                %s = TRUE

                OR EXISTS (
                    SELECT 1

                    FROM client_memberships
                        AS membership

                    WHERE
                        membership.client_id =
                            client.id

                        AND membership.user_id = %s
                )
            )

        ORDER BY client.name;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            (
                is_global_admin,
                user_id,
            ),
        ).fetchall()


def list_available_connections(
    *,
    user_id: int,
    is_global_admin: bool,
) -> list[dict[str, Any]]:
    query = """
        SELECT
            integration.id,
            integration.name,
            integration.provider_type,
            integration.base_url,
            integration.status,
            integration.scope,
            integration.client_id,

            client.name AS client_name

        FROM integration_connections
            AS integration

        LEFT JOIN clients AS client
            ON client.id =
                integration.client_id

        WHERE
            integration.enabled = TRUE

            AND (
                %s = TRUE

                OR integration.scope = 'global'

                OR EXISTS (
                    SELECT 1

                    FROM client_memberships
                        AS membership

                    WHERE
                        membership.client_id =
                            integration.client_id

                        AND membership.user_id = %s
                )
            )

        ORDER BY
            integration.provider_type,
            integration.name;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            (
                is_global_admin,
                user_id,
            ),
        ).fetchall()


def list_environments(
    *,
    user_id: int,
    is_global_admin: bool,
    client_id: int | None,
    environment_type: str | None,
) -> list[dict[str, Any]]:
    query = f"""
        {ENVIRONMENT_SELECT}

        WHERE
            environment.configuration_status
                <> 'archived'

            AND (
                %s = TRUE

                OR EXISTS (
                    SELECT 1

                    FROM client_memberships
                        AS membership

                    WHERE
                        membership.client_id =
                            environment.client_id

                        AND membership.user_id = %s
                )
            )

            AND (
                %s::BIGINT IS NULL

                OR environment.client_id = %s
            )

            AND (
                %s::TEXT IS NULL

                OR environment.environment_type = %s
            )

        ORDER BY
            client.name,
            environment.name;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            (
                is_global_admin,
                user_id,
                client_id,
                client_id,
                environment_type,
                environment_type,
            ),
        ).fetchall()


def find_environment(
    environment_id: int,
) -> dict[str, Any] | None:
    query = f"""
        {ENVIRONMENT_SELECT}

        WHERE environment.id = %s

        LIMIT 1;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            (environment_id,),
        ).fetchone()


def create_environment(
    *,
    client_id: int,
    name: str,
    code: str,
    environment_type: str,
    description: str | None,
    namespace: str,
    domain: str | None,
    connection_ids: dict[str, int],
    user_id: int,
) -> dict[str, Any]:
    """
    Crée un environnement puis associe
    ses connexions techniques.
    """

    with get_database_connection() as connection:
        client = connection.execute(
            """
                SELECT id

                FROM clients

                WHERE
                    id = %s
                    AND status = 'active'

                LIMIT 1;
            """,
            (client_id,),
        ).fetchone()

        if client is None:
            raise ValueError(
                "Le client sélectionné n'existe pas."
            )

        created_environment = connection.execute(
            """
                INSERT INTO deployment_environments (
                    client_id,
                    name,
                    code,
                    environment_type,
                    description,
                    namespace,
                    domain,
                    configuration_status,
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
                    'active',
                    %s
                )
                RETURNING id;
            """,
            (
                client_id,
                name,
                code,
                environment_type,
                description,
                namespace,
                domain,
                user_id,
            ),
        ).fetchone()

        if created_environment is None:
            raise RuntimeError(
                "L'environnement n'a pas été créé."
            )

        environment_id = int(
            created_environment["id"]
        )

        for service_role, connection_id in (
            connection_ids.items()
        ):
            expected_provider = (
                ROLE_PROVIDER_MAP.get(
                    service_role
                )
            )

            if expected_provider is None:
                raise ValueError(
                    "Le rôle de service "
                    f"{service_role} est invalide."
                )

            integration = connection.execute(
                """
                    SELECT id

                    FROM integration_connections

                    WHERE
                        id = %s
                        AND enabled = TRUE
                        AND provider_type = %s

                        AND (
                            scope = 'global'
                            OR client_id = %s
                        )

                    LIMIT 1;
                """,
                (
                    connection_id,
                    expected_provider,
                    client_id,
                ),
            ).fetchone()

            if integration is None:
                raise ValueError(
                    "Une connexion sélectionnée "
                    "est invalide ou appartient "
                    "à un autre client."
                )

            connection.execute(
                """
                    INSERT INTO environment_connections (
                        environment_id,
                        connection_id,
                        service_role,
                        is_required
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s
                    );
                """,
                (
                    environment_id,
                    connection_id,
                    service_role,
                    service_role
                    in REQUIRED_SERVICE_ROLES,
                ),
            )

    created_result = find_environment(
        environment_id
    )

    if created_result is None:
        raise RuntimeError(
            "Impossible de relire "
            "l'environnement créé."
        )

    return created_result