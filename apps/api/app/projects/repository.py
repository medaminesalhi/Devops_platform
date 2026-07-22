from __future__ import annotations

import json
import re

from typing import Any

from app.database import (
    get_database_connection,
)


PROJECT_SELECT = """
    SELECT
        project.id,
        project.name,
        project.slug,
        project.description,
        project.status,

        project.source_connection_id,

        source_connection.name
            AS source_connection_name,

        source_connection.base_url
            AS source_base_url,

        project.repository_url,
        project.repository_path,
        project.repository_visibility,
        project.source_transport,

        project.source_credential_source,
        project.source_auth_method,
        project.source_token_type,
        project.source_username,

        project.default_branch,
        project.source_subdirectory,

        project.source_status,
        project.source_error,
        project.last_source_commit_sha,
        project.last_source_check_at,

        project.default_environment_id,

        default_environment.name
            AS default_environment_name,

        default_environment.environment_type
            AS default_environment_type,

        default_environment.namespace
            AS default_environment_namespace,

        project.created_by,
        project.created_at,
        project.updated_at,

        CASE
            WHEN project.source_credential_source = 'project'
                THEN project_credential.secret_ciphertext
                    IS NOT NULL

            WHEN project.source_credential_source = 'integration'
                THEN integration_credential.secret_ciphertext
                    IS NOT NULL

            ELSE FALSE
        END AS source_credential_configured,

        COALESCE(
            (
                SELECT JSONB_AGG(
                    JSONB_BUILD_OBJECT(
                        'id', environment.id,
                        'name', environment.name,
                        'environmentType',
                            environment.environment_type,
                        'namespace', environment.namespace,
                        'isDefault',
                            project_environment.is_default
                    )
                    ORDER BY
                        project_environment.is_default DESC,
                        environment.name
                )

                FROM project_environments
                    AS project_environment

                INNER JOIN deployment_environments
                    AS environment
                    ON environment.id =
                        project_environment.environment_id

                WHERE
                    project_environment.project_id =
                        project.id
            ),
            '[]'::JSONB
        ) AS environments

    FROM projects AS project

    LEFT JOIN integration_connections
        AS source_connection
        ON source_connection.id =
            project.source_connection_id

    LEFT JOIN integration_credentials
        AS integration_credential
        ON integration_credential.connection_id =
            project.source_connection_id

    LEFT JOIN project_source_credentials
        AS project_credential
        ON project_credential.project_id =
            project.id

    LEFT JOIN deployment_environments
        AS default_environment
        ON default_environment.id =
            project.default_environment_id
"""


def list_git_connections() -> list[dict[str, Any]]:
    query = """
        SELECT
            connection.id,
            connection.name,
            connection.base_url,
            connection.status,
            connection.verify_ssl,
            connection.ssh_host,
            connection.ssh_port,
            connection.ssh_username,

            COALESCE(
                credential.auth_type,
                'none'
            ) AS credential_auth_type,

            credential.username
                AS credential_username,

            (
                credential.secret_ciphertext
                IS NOT NULL
            ) AS credential_configured

        FROM integration_connections
            AS connection

        LEFT JOIN integration_credentials
            AS credential
            ON credential.connection_id =
                connection.id

        WHERE
            connection.provider_type = 'gitlab'
            AND connection.enabled = TRUE

        ORDER BY connection.name;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query
        ).fetchall()


def find_git_connection(
    connection_id: int,
) -> dict[str, Any] | None:
    query = """
        SELECT
            connection.id,
            connection.name,
            connection.base_url,
            connection.verify_ssl,
            connection.ssh_host,
            connection.ssh_port,
            connection.ssh_username,

            COALESCE(
                credential.auth_type,
                'none'
            ) AS credential_auth_type,

            credential.username
                AS credential_username,

            credential.secret_ciphertext,

            (
                credential.secret_ciphertext
                IS NOT NULL
            ) AS credential_configured

        FROM integration_connections
            AS connection

        LEFT JOIN integration_credentials
            AS credential
            ON credential.connection_id =
                connection.id

        WHERE
            connection.id = %s
            AND connection.provider_type = 'gitlab'
            AND connection.enabled = TRUE

        LIMIT 1;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            (connection_id,),
        ).fetchone()


def list_available_environments() -> list[dict[str, Any]]:
    query = """
        SELECT
            id,
            name,
            environment_type,
            namespace,
            domain,
            configuration_status

        FROM deployment_environments

        WHERE configuration_status <> 'archived'

        ORDER BY name;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query
        ).fetchall()


def find_environments(
    environment_ids: list[int],
) -> list[dict[str, Any]]:
    if not environment_ids:
        return []

    query = """
        SELECT
            id,
            name,
            environment_type,
            namespace,
            configuration_status

        FROM deployment_environments

        WHERE
            id = ANY(%s::BIGINT[])
            AND configuration_status <> 'archived';
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            (environment_ids,),
        ).fetchall()


def list_projects(
    *,
    status: str | None,
    search: str | None,
) -> list[dict[str, Any]]:
    query = f"""
        {PROJECT_SELECT}

        WHERE
            project.archived_at IS NULL

            AND (
                %s::TEXT IS NULL
                OR project.status = %s
            )

            AND (
                %s::TEXT IS NULL

                OR project.name ILIKE
                    '%%' || %s || '%%'

                OR project.repository_path ILIKE
                    '%%' || %s || '%%'
            )

        ORDER BY
            project.updated_at DESC,
            project.name;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            (
                status,
                status,

                search,
                search,
                search,
            ),
        ).fetchall()


def find_project(
    project_id: int,
) -> dict[str, Any] | None:
    query = f"""
        {PROJECT_SELECT}

        WHERE project.id = %s

        LIMIT 1;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            (project_id,),
        ).fetchone()


def create_project(
    *,
    name: str,
    description: str | None,

    source_connection_id: int,

    repository_url: str,
    repository_path: str,
    repository_visibility: str,
    source_transport: str,

    credential_source: str,
    auth_method: str,
    token_type: str | None,
    username: str | None,
    encrypted_secret: str | None,

    branch: str,
    source_subdirectory: str | None,

    commit_sha: str,

    environment_ids: list[int],
    default_environment_id: int,

    user_id: int,
) -> dict[str, Any]:
    with get_database_connection() as connection:
        slug = find_available_slug(
            database_connection=connection,
            slug_base=create_slug(name),
        )

        row = connection.execute(
            """
                INSERT INTO projects (
                    name,
                    slug,
                    description,

                    source_provider,
                    source_connection_id,

                    repository_url,
                    repository_path,
                    repository_visibility,
                    source_transport,

                    source_credential_source,
                    source_auth_method,
                    source_token_type,
                    source_username,

                    default_branch,
                    source_subdirectory,

                    source_status,
                    source_error,
                    last_source_commit_sha,
                    last_source_check_at,

                    default_environment_id,

                    status,
                    created_by
                )
                VALUES (
                    %s,
                    %s,
                    %s,

                    'gitlab',
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

                    'valid',
                    NULL,
                    %s,
                    CURRENT_TIMESTAMP,

                    %s,

                    'active',
                    %s
                )
                RETURNING id;
            """,
            (
                name,
                slug,
                description,

                source_connection_id,

                repository_url,
                repository_path,
                repository_visibility,
                source_transport,

                credential_source,
                auth_method,
                token_type,
                username,

                branch,
                source_subdirectory,

                commit_sha,

                default_environment_id,

                user_id,
            ),
        ).fetchone()

        if row is None:
            raise RuntimeError(
                "Le projet n'a pas été créé."
            )

        project_id = int(row["id"])

        if encrypted_secret:
            connection.execute(
                """
                    INSERT INTO project_source_credentials (
                        project_id,
                        secret_ciphertext
                    )
                    VALUES (
                        %s,
                        %s
                    );
                """,
                (
                    project_id,
                    encrypted_secret,
                ),
            )

        for environment_id in environment_ids:
            connection.execute(
                """
                    INSERT INTO project_environments (
                        project_id,
                        environment_id,
                        is_default
                    )
                    VALUES (
                        %s,
                        %s,
                        %s
                    )

                    ON CONFLICT (
                        project_id,
                        environment_id
                    )
                    DO UPDATE SET
                        is_default =
                            EXCLUDED.is_default;
                """,
                (
                    project_id,
                    environment_id,
                    environment_id
                    == default_environment_id,
                ),
            )

        connection.execute(
            """
                INSERT INTO project_activity_logs (
                    project_id,
                    user_id,
                    action,
                    details
                )
                VALUES (
                    %s,
                    %s,
                    'project.created',
                    %s::JSONB
                );
            """,
            (
                project_id,
                user_id,
                json.dumps(
                    {
                        "repositoryPath":
                            repository_path,

                        "visibility":
                            repository_visibility,

                        "transport":
                            source_transport,

                        "credentialSource":
                            credential_source,

                        "authMethod":
                            auth_method,

                        "branch":
                            branch,

                        "commitSha":
                            commit_sha,
                    }
                ),
            ),
        )

        project = connection.execute(
            f"""
                {PROJECT_SELECT}

                WHERE project.id = %s

                LIMIT 1;
            """,
            (project_id,),
        ).fetchone()

        if project is None:
            raise RuntimeError(
                "Impossible de relire le projet."
            )

        return project


def save_source_check(
    *,
    project_id: int | None,
    user_id: int,
    source_connection_id: int,
    repository_path: str,
    branch: str,
    status: str,
    commit_sha: str | None,
    error_code: str | None,
    error_message: str | None,
    details: dict[str, Any],
) -> None:
    query = """
        INSERT INTO project_source_checks (
            project_id,
            user_id,
            source_connection_id,
            repository_path,
            branch,
            status,
            commit_sha,
            error_code,
            error_message,
            details
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
            %s::JSONB
        );
    """

    with get_database_connection() as connection:
        connection.execute(
            query,
            (
                project_id,
                user_id,
                source_connection_id,
                repository_path,
                branch,
                status,
                commit_sha,
                error_code,
                error_message,
                json.dumps(details),
            ),
        )


def create_slug(
    name: str,
) -> str:
    slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        name.lower().strip(),
    )

    return slug.strip("-") or "project"


def find_available_slug(
    *,
    database_connection,
    slug_base: str,
) -> str:
    candidate = slug_base
    suffix = 2

    while True:
        row = database_connection.execute(
            """
                SELECT 1

                FROM projects

                WHERE
                    slug = %s
                    AND archived_at IS NULL

                LIMIT 1;
            """,
            (candidate,),
        ).fetchone()

        if row is None:
            return candidate

        candidate = (
            f"{slug_base}-{suffix}"
        )

        suffix += 1