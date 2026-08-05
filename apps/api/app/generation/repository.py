from __future__ import annotations

import json

from typing import Any

from app.database import (
    get_database_connection,
)


GENERATION_SELECT = """
    SELECT
        generation.id,
        generation.project_id,
        generation.analysis_run_id,
        generation.environment_id,
        generation.status,
        generation.progress,
        generation.current_step,
        generation.summary,
        generation.error_code,
        generation.error_message,
        generation.created_by,
        generation.created_at,
        generation.started_at,
        generation.finished_at,

        project.name AS project_name,
        project.slug AS project_slug,

        analysis.analyzed_commit_sha,
        analysis.confirmed_at
            AS analysis_confirmed_at,

        environment.name
            AS environment_name,

        environment.code
            AS environment_code,

        environment.environment_type,

        environment.namespace
            AS environment_namespace,

        environment.domain
            AS environment_domain

    FROM project_generation_runs
        AS generation

    INNER JOIN projects AS project
        ON project.id =
            generation.project_id

    INNER JOIN project_analysis_runs
        AS analysis
        ON analysis.id =
            generation.analysis_run_id

    INNER JOIN deployment_environments
        AS environment
        ON environment.id =
            generation.environment_id
"""


def find_generation_context(
    project_id: int,
) -> dict[str, Any] | None:
    query = """
        SELECT
            project.id,
            project.name,
            project.slug,
            project.status,

            COALESCE(
                project.source_type,
                'git'
            ) AS source_type,

            COALESCE(
                project.operation_mode,
                'new_application'
            ) AS operation_mode,

            project.source_status,
            project.source_connection_id,

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
            project.last_source_commit_sha,

            project.archive_original_name,
            project.archive_storage_path,
            project.archive_sha256,

            project.default_environment_id,

            environment.name
                AS environment_name,

            environment.code
                AS environment_code,

            environment.environment_type,

            environment.namespace
                AS environment_namespace,

            environment.domain
                AS environment_domain,

            connection.base_url,
            connection.verify_ssl,
            connection.ssh_host,
            connection.ssh_port,
            connection.ssh_username,

            integration_credential.username
                AS integration_username,

            integration_credential.secret_ciphertext
                AS integration_secret_ciphertext,

            project_credential.secret_ciphertext
                AS project_secret_ciphertext,

            confirmed_analysis.id
                AS confirmed_analysis_run_id,

            confirmed_analysis.analyzed_commit_sha
                AS confirmed_version,

            confirmed_analysis.selected_subdirectory
                AS confirmed_subdirectory,

            confirmed_analysis.summary
                AS analysis_summary,

            confirmed_analysis.confirmed_at
                AS analysis_confirmed_at

        FROM projects AS project

        LEFT JOIN deployment_environments
            AS environment
            ON environment.id =
                project.default_environment_id

        LEFT JOIN integration_connections
            AS connection
            ON connection.id =
                project.source_connection_id

        LEFT JOIN integration_credentials
            AS integration_credential
            ON integration_credential.connection_id =
                project.source_connection_id

        LEFT JOIN project_source_credentials
            AS project_credential
            ON project_credential.project_id =
                project.id

        LEFT JOIN LATERAL (
            SELECT analysis.*

            FROM project_analysis_runs
                AS analysis

            WHERE
                analysis.project_id =
                    project.id

                AND analysis.status =
                    'confirmed'

            ORDER BY
                analysis.confirmed_at
                    DESC NULLS LAST,

                analysis.created_at DESC

            LIMIT 1
        ) AS confirmed_analysis
            ON TRUE

        WHERE
            project.id = %s

            AND project.archived_at
                IS NULL

        LIMIT 1;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            (project_id,),
        ).fetchone()


def list_confirmed_components(
    analysis_run_id: int,
) -> list[dict[str, Any]]:
    query = """
        SELECT *

        FROM project_components

        WHERE
            analysis_run_id = %s
            AND deployable = TRUE

        ORDER BY
            root_path,
            name;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            (analysis_run_id,),
        ).fetchall()


def find_active_generation(
    project_id: int,
) -> dict[str, Any] | None:
    query = """
        SELECT
            id,
            project_id,
            status,
            progress,
            current_step,
            created_at

        FROM project_generation_runs

        WHERE
            project_id = %s

            AND status IN (
                'pending',
                'running'
            )

        ORDER BY created_at DESC

        LIMIT 1;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            (project_id,),
        ).fetchone()


def create_generation_run(
    *,
    project_id: int,
    analysis_run_id: int,
    environment_id: int,
    created_by: int,
) -> dict[str, Any]:
    with get_database_connection() as connection:
        row = connection.execute(
            """
                INSERT INTO project_generation_runs (
                    project_id,
                    analysis_run_id,
                    environment_id,
                    status,
                    progress,
                    current_step,
                    created_by
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    'pending',
                    0,
                    'pending',
                    %s
                )
                RETURNING *;
            """,
            (
                project_id,
                analysis_run_id,
                environment_id,
                created_by,
            ),
        ).fetchone()

        if row is None:
            raise RuntimeError(
                "Impossible de créer la génération."
            )

        connection.execute(
            """
                UPDATE projects

                SET
                    generation_status =
                        'pending',

                    latest_generation_run_id =
                        %s,

                    updated_at =
                        CURRENT_TIMESTAMP

                WHERE id = %s;
            """,
            (
                row["id"],
                project_id,
            ),
        )

        return row


def find_generation_run(
    generation_run_id: int,
) -> dict[str, Any] | None:
    query = f"""
        {GENERATION_SELECT}

        WHERE generation.id = %s

        LIMIT 1;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            (generation_run_id,),
        ).fetchone()


def find_generation_for_project(
    *,
    project_id: int,
    generation_run_id: int,
) -> dict[str, Any] | None:
    query = f"""
        {GENERATION_SELECT}

        WHERE
            generation.id = %s
            AND generation.project_id = %s

        LIMIT 1;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            (
                generation_run_id,
                project_id,
            ),
        ).fetchone()


def find_latest_generation(
    project_id: int,
) -> dict[str, Any] | None:
    query = f"""
        {GENERATION_SELECT}

        WHERE generation.project_id = %s

        ORDER BY
            generation.created_at DESC

        LIMIT 1;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            (project_id,),
        ).fetchone()


def update_generation_progress(
    *,
    generation_run_id: int,
    project_id: int,
    progress: int,
    current_step: str,
) -> None:
    with get_database_connection() as connection:
        connection.execute(
            """
                UPDATE project_generation_runs

                SET
                    status = 'running',
                    progress = %s,
                    current_step = %s,

                    started_at = COALESCE(
                        started_at,
                        CURRENT_TIMESTAMP
                    )

                WHERE id = %s;
            """,
            (
                progress,
                current_step,
                generation_run_id,
            ),
        )

        connection.execute(
            """
                UPDATE projects

                SET
                    generation_status =
                        'running',

                    latest_generation_run_id =
                        %s,

                    updated_at =
                        CURRENT_TIMESTAMP

                WHERE id = %s;
            """,
            (
                generation_run_id,
                project_id,
            ),
        )


def replace_generation_artifacts(
    *,
    generation_run_id: int,
    project_id: int,
    artifacts: list[dict[str, Any]],
) -> None:
    with get_database_connection() as connection:
        connection.execute(
            """
                DELETE FROM
                    project_generated_artifacts

                WHERE generation_run_id = %s;
            """,
            (generation_run_id,),
        )

        for artifact in artifacts:
            connection.execute(
                """
                    INSERT INTO
                        project_generated_artifacts (
                            generation_run_id,
                            project_id,
                            component_id,
                            artifact_type,
                            relative_path,
                            content,
                            original_content,
                            content_sha256,
                            artifact_status,
                            review_status,
                            metadata
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
                        'pending_review',
                        %s::JSONB
                    );
                """,
                (
                    generation_run_id,
                    project_id,
                    artifact.get(
                        "component_id"
                    ),
                    artifact[
                        "artifact_type"
                    ],
                    artifact[
                        "relative_path"
                    ],
                    artifact["content"],
                    artifact.get(
                        "original_content"
                    ),
                    artifact[
                        "content_sha256"
                    ],
                    artifact[
                        "artifact_status"
                    ],
                    json.dumps(
                        artifact.get(
                            "metadata"
                        )
                        or {}
                    ),
                ),
            )


def complete_generation(
    *,
    generation_run_id: int,
    project_id: int,
    summary: dict[str, Any],
) -> None:
    with get_database_connection() as connection:
        connection.execute(
            """
                UPDATE project_generation_runs

                SET
                    status = 'completed',
                    progress = 100,
                    current_step = 'completed',
                    summary = %s::JSONB,
                    error_code = NULL,
                    error_message = NULL,
                    finished_at =
                        CURRENT_TIMESTAMP

                WHERE id = %s;
            """,
            (
                json.dumps(summary),
                generation_run_id,
            ),
        )

        connection.execute(
            """
                UPDATE projects

                SET
                    generation_status =
                        'completed',

                    latest_generation_run_id =
                        %s,

                    updated_at =
                        CURRENT_TIMESTAMP

                WHERE id = %s;
            """,
            (
                generation_run_id,
                project_id,
            ),
        )


def fail_generation(
    *,
    generation_run_id: int,
    project_id: int,
    error_code: str,
    error_message: str,
) -> None:
    with get_database_connection() as connection:
        connection.execute(
            """
                UPDATE project_generation_runs

                SET
                    status = 'failed',
                    current_step = 'failed',
                    error_code = %s,
                    error_message = %s,
                    finished_at =
                        CURRENT_TIMESTAMP

                WHERE id = %s;
            """,
            (
                error_code,
                error_message,
                generation_run_id,
            ),
        )

        connection.execute(
            """
                UPDATE projects

                SET
                    generation_status =
                        'failed',

                    latest_generation_run_id =
                        %s,

                    updated_at =
                        CURRENT_TIMESTAMP

                WHERE id = %s;
            """,
            (
                generation_run_id,
                project_id,
            ),
        )


def add_generation_event(
    *,
    generation_run_id: int,
    level: str,
    step: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    query = """
        INSERT INTO project_generation_events (
            generation_run_id,
            level,
            step,
            message,
            details
        )
        VALUES (
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
                generation_run_id,
                level,
                step,
                message,
                json.dumps(details or {}),
            ),
        )


def list_generation_events(
    *,
    generation_run_id: int,
    after_id: int,
) -> list[dict[str, Any]]:
    query = """
        SELECT *

        FROM project_generation_events

        WHERE
            generation_run_id = %s
            AND id > %s

        ORDER BY id;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            (
                generation_run_id,
                after_id,
            ),
        ).fetchall()


def list_generation_artifacts(
    generation_run_id: int,
) -> list[dict[str, Any]]:
    query = """
        SELECT
            artifact.id,
            artifact.generation_run_id,
            artifact.project_id,
            artifact.component_id,
            artifact.artifact_type,
            artifact.relative_path,
            artifact.content_sha256,
            artifact.artifact_status,
            artifact.review_status,
            artifact.metadata,
            artifact.created_at,
            artifact.updated_at,

            component.name
                AS component_name,

            component.root_path
                AS component_root_path

        FROM project_generated_artifacts
            AS artifact

        LEFT JOIN project_components
            AS component
            ON component.id =
                artifact.component_id

        WHERE
            artifact.generation_run_id = %s

        ORDER BY
            artifact.relative_path;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            (generation_run_id,),
        ).fetchall()


def find_generation_artifact(
    *,
    generation_run_id: int,
    artifact_id: int,
) -> dict[str, Any] | None:
    query = """
        SELECT
            artifact.*,

            component.name
                AS component_name,

            component.root_path
                AS component_root_path

        FROM project_generated_artifacts
            AS artifact

        LEFT JOIN project_components
            AS component
            ON component.id =
                artifact.component_id

        WHERE
            artifact.generation_run_id = %s
            AND artifact.id = %s

        LIMIT 1;
    """

    with get_database_connection() as connection:
        return connection.execute(
            query,
            (
                generation_run_id,
                artifact_id,
            ),
        ).fetchone()