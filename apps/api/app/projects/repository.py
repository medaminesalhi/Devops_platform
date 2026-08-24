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
        project.operation_mode,
        project.source_type,
        project.source_provider,
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

        project.archive_original_name,
        project.archive_stored_name,
        project.archive_size_bytes,
        project.archive_sha256,
        project.archive_entry_count,
        project.archive_uncompressed_bytes,

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
            WHEN project.source_type = 'zip'
                THEN TRUE

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
                    ORDER BY environment.name
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


def list_git_connections(
    *,
    owner_user_id: int | None = None,
) -> list[dict[str, Any]]:
    query = """
        SELECT
            connection.id,
            connection.name,
            connection.provider_type,
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
            connection.provider_type IN ('gitlab', 'github')
            AND connection.enabled = TRUE
            AND (
                %s::BIGINT IS NULL
                OR connection.created_by = %s
            )

        ORDER BY connection.name;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            (owner_user_id, owner_user_id),
        ).fetchall()


def find_git_connection(
    connection_id: int,
    *,
    owner_user_id: int | None = None,
) -> dict[str, Any] | None:
    query = """
        SELECT
            connection.id,
            connection.name,
            connection.provider_type,
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
            AND connection.provider_type IN ('gitlab', 'github')
            AND connection.enabled = TRUE
            AND (
                %s::BIGINT IS NULL
                OR connection.created_by = %s
            )

        LIMIT 1;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            (connection_id, owner_user_id, owner_user_id),
        ).fetchone()


def list_available_environments(
    *,
    owner_user_id: int | None = None,
) -> list[dict[str, Any]]:
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
          AND (
              %s::BIGINT IS NULL
              OR created_by = %s
          )

        ORDER BY name;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            (owner_user_id, owner_user_id),
        ).fetchall()


def find_environment(
    environment_id: int,
    *,
    owner_user_id: int | None = None,
) -> dict[str, Any] | None:
    query = """
        SELECT
            id,
            name,
            environment_type,
            namespace,
            domain,
            configuration_status

        FROM deployment_environments

        WHERE
            id = %s
            AND configuration_status <> 'archived'
            AND (
                %s::BIGINT IS NULL
                OR created_by = %s
            )

        LIMIT 1;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            (environment_id, owner_user_id, owner_user_id),
        ).fetchone()


def list_projects(
    *,
    status: str | None,
    search: str | None,
    owner_user_id: int | None = None,
) -> list[dict[str, Any]]:
    query = f"""
        {PROJECT_SELECT}

        WHERE
            project.archived_at IS NULL

            AND (
                %s::BIGINT IS NULL
                OR project.created_by = %s
            )

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

                OR project.archive_original_name ILIKE
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
                owner_user_id,
                owner_user_id,
                status,
                status,
                search,
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


def create_git_project(
    *,
    name: str,
    description: str | None,
    operation_mode: str,

    source_connection_id: int,
    source_provider: str,

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

    environment_id: int,
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
                    operation_mode,
                    source_type,
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
                    %s,
                    'git',
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
                operation_mode,
                source_provider,
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
                environment_id,
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

        attach_environment(
            database_connection=connection,
            project_id=project_id,
            environment_id=environment_id,
        )

        add_project_activity(
            database_connection=connection,
            project_id=project_id,
            user_id=user_id,
            action="project.created",
            details={
                "operationMode": operation_mode,
                "sourceType": "git",
                "repositoryPath": repository_path,
                "visibility": repository_visibility,
                "transport": source_transport,
                "credentialSource": credential_source,
                "authMethod": auth_method,
                "branch": branch,
                "commitSha": commit_sha,
                "environmentId": environment_id,
            },
        )

        return read_project_in_transaction(
            database_connection=connection,
            project_id=project_id,
        )


def create_zip_project(
    *,
    name: str,
    description: str | None,
    operation_mode: str,
    source_subdirectory: str | None,

    archive_original_name: str,
    archive_stored_name: str,
    archive_storage_path: str,
    archive_size_bytes: int,
    archive_sha256: str,
    archive_entry_count: int,
    archive_uncompressed_bytes: int,

    environment_id: int,
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
                    operation_mode,
                    source_type,
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
                    archive_original_name,
                    archive_stored_name,
                    archive_storage_path,
                    archive_size_bytes,
                    archive_sha256,
                    archive_entry_count,
                    archive_uncompressed_bytes,
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
                    %s,
                    'zip',
                    'archive',
                    NULL,
                    NULL,
                    NULL,
                    NULL,
                    NULL,
                    NULL,
                    NULL,
                    NULL,
                    NULL,
                    NULL,
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
                    NULL,
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
                operation_mode,
                source_subdirectory,
                archive_original_name,
                archive_stored_name,
                archive_storage_path,
                archive_size_bytes,
                archive_sha256,
                archive_entry_count,
                archive_uncompressed_bytes,
                environment_id,
                user_id,
            ),
        ).fetchone()

        if row is None:
            raise RuntimeError(
                "Le projet n'a pas été créé."
            )

        project_id = int(row["id"])

        attach_environment(
            database_connection=connection,
            project_id=project_id,
            environment_id=environment_id,
        )

        add_project_activity(
            database_connection=connection,
            project_id=project_id,
            user_id=user_id,
            action="project.created",
            details={
                "operationMode": operation_mode,
                "sourceType": "zip",
                "archiveName": archive_original_name,
                "archiveSha256": archive_sha256,
                "environmentId": environment_id,
            },
        )

        return read_project_in_transaction(
            database_connection=connection,
            project_id=project_id,
        )


def attach_environment(
    *,
    database_connection,
    project_id: int,
    environment_id: int,
) -> None:
    database_connection.execute(
        """
            INSERT INTO project_environments (
                project_id,
                environment_id,
                is_default
            )
            VALUES (
                %s,
                %s,
                TRUE
            )

            ON CONFLICT (
                project_id,
                environment_id
            )
            DO UPDATE SET
                is_default = TRUE;
        """,
        (
            project_id,
            environment_id,
        ),
    )


def add_project_activity(
    *,
    database_connection,
    project_id: int,
    user_id: int,
    action: str,
    details: dict[str, Any],
) -> None:
    database_connection.execute(
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
                %s,
                %s::JSONB
            );
        """,
        (
            project_id,
            user_id,
            action,
            json.dumps(details),
        ),
    )


def read_project_in_transaction(
    *,
    database_connection,
    project_id: int,
) -> dict[str, Any]:
    project = database_connection.execute(
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
    source_connection_id: int | None,
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

        candidate = f"{slug_base}-{suffix}"

        suffix += 1

def delete_project(project_id: int) -> dict[str, Any] | None:
    """Supprime définitivement un projet et ses données liées en cascade."""
    with get_database_connection() as connection:
        row = connection.execute(
            """
                DELETE FROM projects
                WHERE id = %s
                RETURNING id, name;
            """,
            (project_id,),
        ).fetchone()

    return row
