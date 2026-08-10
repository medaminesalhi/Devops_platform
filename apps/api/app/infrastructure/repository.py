from __future__ import annotations

from typing import Any

from app.database import get_database_connection


# ============================================================
# FOURNISSEURS AUTORISÉS PAR RÔLE
# ============================================================

ROLE_PROVIDER_MAP: dict[str, set[str]] = {
    "kubernetes": {
        "kubernetes",
    },

    "argocd": {
        "argocd",
    },

    "container_registry": {
        "nexus",
    },

    "gitops_repository": {
        "gitlab",
    },

    "storage": {
        "nfs",
    },

    "ai_provider": {
        "ollama",
        "litellm",
        "vllm",
        "openai_compatible",
    },

    "custom_http_service": {
        "generic_http",
    },
}


REQUIRED_SERVICE_ROLES = {
    "kubernetes",
    "argocd",
    "container_registry",
}


# ============================================================
# REQUÊTE DE BASE DES ENVIRONNEMENTS
# ============================================================

ENVIRONMENT_SELECT = """
    SELECT
        environment.id,
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


        (
            SELECT
                COUNT(*)::INTEGER

            FROM environment_connections
                AS service_link

            WHERE
                service_link.environment_id =
                    environment.id
        ) AS service_total,


        (
            SELECT
                COUNT(*)::INTEGER

            FROM environment_connections
                AS service_link

            INNER JOIN integration_connections
                AS integration

                ON integration.id =
                    service_link.connection_id

            WHERE
                service_link.environment_id =
                    environment.id

                AND integration.status =
                    'online'
        ) AS service_online,


        (
            SELECT
                COUNT(*)::INTEGER

            FROM project_environments
                AS project_environment

            WHERE
                project_environment.environment_id =
                    environment.id
        ) AS project_count,


        (
            SELECT
                integration.name

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
            SELECT
                MAX(
                    integration.last_checked_at
                )

            FROM environment_connections
                AS service_link

            INNER JOIN integration_connections
                AS integration

                ON integration.id =
                    service_link.connection_id

            WHERE
                service_link.environment_id =
                    environment.id
        ) AS last_checked_at,


        CASE
            WHEN
                environment.configuration_status =
                    'archived'

            THEN
                'archived'


            WHEN (
                SELECT
                    COUNT(*)

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

            THEN
                'draft'


            WHEN EXISTS (
                SELECT
                    1

                FROM environment_connections
                    AS required_link

                INNER JOIN integration_connections
                    AS integration

                    ON integration.id =
                        required_link.connection_id

                WHERE
                    required_link.environment_id =
                        environment.id

                    AND required_link.is_required =
                        TRUE

                    AND integration.status =
                        'offline'
            )

            THEN
                'offline'


            WHEN EXISTS (
                SELECT
                    1

                FROM environment_connections
                    AS service_link

                INNER JOIN integration_connections
                    AS integration

                    ON integration.id =
                        service_link.connection_id

                WHERE
                    service_link.environment_id =
                        environment.id

                    AND integration.status IN (
                        'degraded',
                        'unchecked',
                        'not_configured',
                        'offline'
                    )
            )

            THEN
                'degraded'


            ELSE
                'ready'
        END AS effective_status,


        COALESCE(
            (
                SELECT
                    JSONB_AGG(
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

                            'baseUrl',
                                integration.base_url,

                            'status',
                                integration.status,

                            'lastCheckedAt',
                                integration.last_checked_at,

                            'lastLatencyMs',
                                integration.last_latency_ms
                        )

                        ORDER BY
                            service_link.is_required
                                DESC,

                            service_link.service_role
                    )

                FROM environment_connections
                    AS service_link

                INNER JOIN integration_connections
                    AS integration

                    ON integration.id =
                        service_link.connection_id

                WHERE
                    service_link.environment_id =
                        environment.id
            ),

            '[]'::JSONB
        ) AS services


    FROM deployment_environments
        AS environment
"""


# ============================================================
# INTÉGRATIONS DISPONIBLES
# ============================================================

def list_available_connections(
) -> list[dict[str, Any]]:
    query = """
        SELECT
            integration.id,
            integration.name,
            integration.provider_type,
            integration.base_url,
            integration.description,
            integration.status,
            integration.last_checked_at,
            integration.last_latency_ms

        FROM integration_connections
            AS integration

        WHERE
            integration.enabled = TRUE

        ORDER BY
            integration.provider_type,
            integration.name;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query
        ).fetchall()


# ============================================================
# LISTE DES ENVIRONNEMENTS
# ============================================================

def list_environments(
    *,
    environment_type: str | None,
) -> list[dict[str, Any]]:
    query = f"""
        {ENVIRONMENT_SELECT}

        WHERE
            environment.configuration_status
                <> 'archived'

            AND (
                %s::TEXT IS NULL

                OR environment.environment_type =
                    %s
            )

        ORDER BY
            environment.name;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            (
                environment_type,
                environment_type,
            ),
        ).fetchall()


# ============================================================
# TROUVER UN ENVIRONNEMENT
# ============================================================

def find_environment(
    environment_id: int,
) -> dict[str, Any] | None:
    query = f"""
        {ENVIRONMENT_SELECT}

        WHERE
            environment.id = %s

        LIMIT 1;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            (
                environment_id,
            ),
        ).fetchone()


# ============================================================
# VALIDATION DES CONNEXIONS
# ============================================================

def _validate_connections(
    database_connection,
    *,
    connection_ids: dict[str, int],
) -> list[tuple[str, int, bool]]:
    validated: list[
        tuple[str, int, bool]
    ] = []


    for (
        service_role,
        connection_id,
    ) in connection_ids.items():
        allowed_providers = (
            ROLE_PROVIDER_MAP.get(
                service_role
            )
        )


        if allowed_providers is None:
            raise ValueError(
                (
                    "Le rôle de service "
                    f"« {service_role} » "
                    "n'est pas supporté."
                )
            )


        integration = database_connection.execute(
            """
                SELECT
                    id,
                    provider_type

                FROM integration_connections

                WHERE
                    id = %s

                    AND enabled = TRUE

                LIMIT 1;
            """,
            (
                connection_id,
            ),
        ).fetchone()


        if integration is None:
            raise ValueError(
                (
                    "Une connexion sélectionnée "
                    "est introuvable ou désactivée."
                )
            )


        provider_type = str(
            integration[
                "provider_type"
            ]
        )


        if (
            provider_type
            not in allowed_providers
        ):
            expected = ", ".join(
                sorted(
                    allowed_providers
                )
            )

            raise ValueError(
                (
                    "La connexion du rôle "
                    f"« {service_role} » "
                    "utilise le fournisseur "
                    f"« {provider_type} ». "
                    "Fournisseur attendu : "
                    f"{expected}."
                )
            )


        validated.append(
            (
                service_role,

                int(
                    connection_id
                ),

                service_role
                in REQUIRED_SERVICE_ROLES,
            )
        )


    return validated


# ============================================================
# REMPLACER LES CONNEXIONS D'UN ENVIRONNEMENT
# ============================================================

def _replace_environment_connections(
    database_connection,
    *,
    environment_id: int,
    validated_connections: list[
        tuple[str, int, bool]
    ],
) -> None:
    database_connection.execute(
        """
            DELETE FROM
                environment_connections

            WHERE
                environment_id = %s;
        """,
        (
            environment_id,
        ),
    )


    for (
        service_role,
        connection_id,
        is_required,
    ) in validated_connections:
        database_connection.execute(
            """
                INSERT INTO
                    environment_connections (
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
                is_required,
            ),
        )


# ============================================================
# CRÉER UN ENVIRONNEMENT
# ============================================================

def create_environment(
    *,
    name: str,
    code: str,
    environment_type: str,
    description: str | None,
    namespace: str,
    domain: str | None,
    connection_ids: dict[str, int],
    user_id: int,
) -> dict[str, Any]:
    with get_database_connection() as connection:
        validated_connections = (
            _validate_connections(
                connection,
                connection_ids=
                    connection_ids,
            )
        )


        created_environment = (
            connection.execute(
                """
                    INSERT INTO
                        deployment_environments (
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
                        'active',
                        %s
                    )

                    RETURNING id;
                """,
                (
                    name,
                    code,
                    environment_type,
                    description,
                    namespace,
                    domain,
                    user_id,
                ),
            ).fetchone()
        )


        if created_environment is None:
            raise RuntimeError(
                (
                    "L'environnement "
                    "n'a pas été créé."
                )
            )


        environment_id = int(
            created_environment[
                "id"
            ]
        )


        _replace_environment_connections(
            connection,
            environment_id=
                environment_id,

            validated_connections=
                validated_connections,
        )


    result = find_environment(
        environment_id
    )


    if result is None:
        raise RuntimeError(
            (
                "Impossible de relire "
                "l'environnement créé."
            )
        )


    return result


# ============================================================
# MODIFIER UN ENVIRONNEMENT
# ============================================================

def update_environment(
    *,
    environment_id: int,
    name: str,
    code: str,
    environment_type: str,
    description: str | None,
    namespace: str,
    domain: str | None,
    connection_ids: dict[str, int],
) -> dict[str, Any]:
    with get_database_connection() as connection:
        existing = connection.execute(
            """
                SELECT
                    id

                FROM deployment_environments

                WHERE
                    id = %s

                    AND configuration_status
                        <> 'archived'

                LIMIT 1;
            """,
            (
                environment_id,
            ),
        ).fetchone()


        if existing is None:
            raise ValueError(
                (
                    "L'environnement "
                    "est introuvable."
                )
            )


        validated_connections = (
            _validate_connections(
                connection,
                connection_ids=
                    connection_ids,
            )
        )


        connection.execute(
            """
                UPDATE deployment_environments

                SET
                    name = %s,
                    code = %s,
                    environment_type = %s,
                    description = %s,
                    namespace = %s,
                    domain = %s,
                    configuration_status =
                        'active'

                WHERE
                    id = %s;
            """,
            (
                name,
                code,
                environment_type,
                description,
                namespace,
                domain,
                environment_id,
            ),
        )


        _replace_environment_connections(
            connection,
            environment_id=
                environment_id,

            validated_connections=
                validated_connections,
        )


    result = find_environment(
        environment_id
    )


    if result is None:
        raise RuntimeError(
            (
                "Impossible de relire "
                "l'environnement modifié."
            )
        )


    return result


# ============================================================
# ARCHIVER UN ENVIRONNEMENT
# ============================================================

def archive_environment(
    *,
    environment_id: int,
) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        environment = connection.execute(
            """
                UPDATE deployment_environments

                SET
                    configuration_status =
                        'archived'

                WHERE
                    id = %s

                    AND configuration_status
                        <> 'archived'

                RETURNING
                    id,
                    name;
            """,
            (
                environment_id,
            ),
        ).fetchone()


        if environment is None:
            return None


        return {
            "id":
                int(
                    environment["id"]
                ),

            "name":
                environment["name"],
        }