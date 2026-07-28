from __future__ import annotations

import json
from typing import Any

from app.database import get_database_connection


def find_project_source(project_id: int) -> dict[str, Any] | None:
    query = """
        SELECT
            project.id,
            project.name,
            project.slug,
            project.status,
            project.source_status,
            COALESCE(project.source_type, 'git') AS source_type,
            COALESCE(project.operation_mode, 'new_application') AS operation_mode,

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
            project.archive_size_bytes,
            project.archive_sha256,
            project.archive_entry_count,
            project.archive_uncompressed_bytes,

            project.default_environment_id,
            environment.name AS environment_name,
            environment.namespace AS environment_namespace,

            connection.name AS connection_name,
            connection.base_url,
            connection.verify_ssl,
            connection.ssh_host,
            connection.ssh_port,
            connection.ssh_username,

            integration_credential.auth_type AS integration_auth_type,
            integration_credential.username AS integration_username,
            integration_credential.secret_ciphertext AS integration_secret_ciphertext,
            project_credential.secret_ciphertext AS project_secret_ciphertext,

            previous_analysis.analyzed_commit_sha AS previous_analyzed_version

        FROM projects AS project

        LEFT JOIN integration_connections AS connection
            ON connection.id = project.source_connection_id

        LEFT JOIN integration_credentials AS integration_credential
            ON integration_credential.connection_id = project.source_connection_id

        LEFT JOIN project_source_credentials AS project_credential
            ON project_credential.project_id = project.id

        LEFT JOIN deployment_environments AS environment
            ON environment.id = project.default_environment_id

        LEFT JOIN LATERAL (
            SELECT analysis.analyzed_commit_sha
            FROM project_analysis_runs AS analysis
            WHERE analysis.project_id = project.id
              AND analysis.status IN ('completed', 'confirmed')
              AND analysis.analyzed_commit_sha IS NOT NULL
            ORDER BY analysis.created_at DESC
            LIMIT 1
        ) AS previous_analysis ON TRUE

        WHERE project.id = %s
          AND project.archived_at IS NULL
        LIMIT 1;
    """

    with get_database_connection() as connection:
        return connection.execute(query, (project_id,)).fetchone()


def find_active_analysis(project_id: int) -> dict[str, Any] | None:
    query = """
        SELECT id, project_id, status, progress, current_step, created_at
        FROM project_analysis_runs
        WHERE project_id = %s
          AND status IN ('pending', 'preparing', 'cloning', 'analyzing')
        ORDER BY created_at DESC
        LIMIT 1;
    """

    with get_database_connection() as connection:
        return connection.execute(query, (project_id,)).fetchone()


def create_analysis_run(
    *,
    project_id: int,
    commit_policy: str,
    requested_commit_sha: str | None,
    selected_subdirectory: str | None,
    created_by: int,
) -> dict[str, Any]:
    with get_database_connection() as connection:
        row = connection.execute(
            """
                INSERT INTO project_analysis_runs (
                    project_id,
                    commit_policy,
                    requested_commit_sha,
                    selected_subdirectory,
                    status,
                    progress,
                    current_step,
                    created_by
                )
                VALUES (%s, %s, %s, %s, 'pending', 0, 'pending', %s)
                RETURNING *;
            """,
            (
                project_id,
                commit_policy,
                requested_commit_sha,
                selected_subdirectory,
                created_by,
            ),
        ).fetchone()

        if row is None:
            raise RuntimeError("Impossible de créer l'analyse.")

        connection.execute(
            """
                UPDATE projects
                SET latest_analysis_run_id = %s,
                    analysis_status = 'pending',
                    analysis_confirmed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s;
            """,
            (row["id"], project_id),
        )

        return row


def find_analysis_run(analysis_run_id: int) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        return connection.execute(
            "SELECT * FROM project_analysis_runs WHERE id = %s LIMIT 1;",
            (analysis_run_id,),
        ).fetchone()


def find_analysis_for_project(
    *,
    project_id: int,
    analysis_run_id: int,
) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        return connection.execute(
            """
                SELECT *
                FROM project_analysis_runs
                WHERE id = %s AND project_id = %s
                LIMIT 1;
            """,
            (analysis_run_id, project_id),
        ).fetchone()


def find_latest_analysis(project_id: int) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        return connection.execute(
            """
                SELECT *
                FROM project_analysis_runs
                WHERE project_id = %s
                ORDER BY created_at DESC
                LIMIT 1;
            """,
            (project_id,),
        ).fetchone()


def update_analysis_progress(
    *,
    analysis_run_id: int,
    status: str,
    progress: int,
    current_step: str,
    branch_head_sha: str | None = None,
    analyzed_commit_sha: str | None = None,
) -> None:
    with get_database_connection() as connection:
        connection.execute(
            """
                UPDATE project_analysis_runs
                SET status = %s,
                    progress = %s,
                    current_step = %s,
                    branch_head_sha = COALESCE(%s, branch_head_sha),
                    analyzed_commit_sha = COALESCE(%s, analyzed_commit_sha),
                    started_at = COALESCE(started_at, CURRENT_TIMESTAMP)
                WHERE id = %s;
            """,
            (
                status,
                progress,
                current_step,
                branch_head_sha,
                analyzed_commit_sha,
                analysis_run_id,
            ),
        )


def complete_analysis(
    *,
    analysis_run_id: int,
    project_id: int,
    source_type: str,
    branch_head_sha: str,
    analyzed_commit_sha: str,
    summary: dict[str, Any],
) -> None:
    with get_database_connection() as connection:
        connection.execute(
            """
                UPDATE project_analysis_runs
                SET status = 'completed',
                    progress = 100,
                    current_step = 'completed',
                    branch_head_sha = %s,
                    analyzed_commit_sha = %s,
                    summary = %s::JSONB,
                    error_code = NULL,
                    error_message = NULL,
                    finished_at = CURRENT_TIMESTAMP
                WHERE id = %s;
            """,
            (
                branch_head_sha,
                analyzed_commit_sha,
                json.dumps(summary),
                analysis_run_id,
            ),
        )

        connection.execute(
            """
                UPDATE projects
                SET analysis_status = 'completed',
                    latest_analysis_run_id = %s,
                    last_source_commit_sha = CASE
                        WHEN %s = 'git' THEN %s
                        ELSE last_source_commit_sha
                    END,
                    last_source_check_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s;
            """,
            (
                analysis_run_id,
                source_type,
                analyzed_commit_sha,
                project_id,
            ),
        )


def fail_analysis(
    *,
    analysis_run_id: int,
    project_id: int,
    error_code: str,
    error_message: str,
) -> None:
    with get_database_connection() as connection:
        connection.execute(
            """
                UPDATE project_analysis_runs
                SET status = 'failed',
                    current_step = 'failed',
                    error_code = %s,
                    error_message = %s,
                    finished_at = CURRENT_TIMESTAMP
                WHERE id = %s;
            """,
            (error_code, error_message, analysis_run_id),
        )

        connection.execute(
            """
                UPDATE projects
                SET analysis_status = 'failed',
                    latest_analysis_run_id = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s;
            """,
            (analysis_run_id, project_id),
        )


def add_analysis_event(
    *,
    analysis_run_id: int,
    level: str,
    step: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    with get_database_connection() as connection:
        connection.execute(
            """
                INSERT INTO project_analysis_events (
                    analysis_run_id,
                    level,
                    step,
                    message,
                    details
                )
                VALUES (%s, %s, %s, %s, %s::JSONB);
            """,
            (
                analysis_run_id,
                level,
                step,
                message,
                json.dumps(details or {}),
            ),
        )


def list_analysis_events(
    *,
    analysis_run_id: int,
    after_id: int = 0,
) -> list[dict[str, Any]]:
    with get_database_connection() as connection:
        return connection.execute(
            """
                SELECT id, analysis_run_id, level, step, message, details, created_at
                FROM project_analysis_events
                WHERE analysis_run_id = %s AND id > %s
                ORDER BY id;
            """,
            (analysis_run_id, after_id),
        ).fetchall()


def replace_analysis_components(
    *,
    project_id: int,
    analysis_run_id: int,
    components: list[dict[str, Any]],
) -> None:
    with get_database_connection() as connection:
        connection.execute(
            "DELETE FROM project_components WHERE analysis_run_id = %s;",
            (analysis_run_id,),
        )

        for component in components:
            connection.execute(
                """
                    INSERT INTO project_components (
                        project_id,
                        analysis_run_id,
                        name,
                        component_type,
                        root_path,
                        runtime,
                        framework,
                        package_manager,
                        build_command,
                        start_command,
                        detected_port,
                        deployable,
                        dockerfile_path,
                        helm_chart_path,
                        kubernetes_paths,
                        environment_variables,
                        confidence,
                        configuration
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s::JSONB, %s::JSONB,
                        %s, %s::JSONB
                    );
                """,
                (
                    project_id,
                    analysis_run_id,
                    component["name"],
                    component["component_type"],
                    component["root_path"],
                    component.get("runtime"),
                    component.get("framework"),
                    component.get("package_manager"),
                    component.get("build_command"),
                    component.get("start_command"),
                    component.get("detected_port"),
                    component.get("deployable", True),
                    component.get("dockerfile_path"),
                    component.get("helm_chart_path"),
                    json.dumps(component.get("kubernetes_paths", [])),
                    json.dumps(component.get("environment_variables", [])),
                    component.get("confidence", 0),
                    json.dumps(component.get("configuration", {})),
                ),
            )


def list_analysis_components(analysis_run_id: int) -> list[dict[str, Any]]:
    with get_database_connection() as connection:
        return connection.execute(
            """
                SELECT
                    id,
                    project_id,
                    analysis_run_id,
                    name,
                    component_type,
                    root_path,
                    runtime,
                    framework,
                    package_manager,
                    build_command,
                    start_command,
                    detected_port,
                    deployable,
                    dockerfile_path,
                    helm_chart_path,
                    kubernetes_paths,
                    environment_variables,
                    confidence,
                    configuration,
                    user_modified,
                    created_at,
                    updated_at
                FROM project_components
                WHERE analysis_run_id = %s
                ORDER BY root_path, name;
            """,
            (analysis_run_id,),
        ).fetchall()


def update_component(
    *,
    component_id: int,
    analysis_run_id: int,
    changes: dict[str, Any],
) -> dict[str, Any] | None:
    allowed_columns = {
        "name": "name",
        "component_type": "component_type",
        "runtime": "runtime",
        "framework": "framework",
        "package_manager": "package_manager",
        "build_command": "build_command",
        "start_command": "start_command",
        "detected_port": "detected_port",
        "deployable": "deployable",
    }

    assignments: list[str] = []
    values: list[Any] = []

    for field_name, value in changes.items():
        column_name = allowed_columns.get(field_name)
        if column_name is None:
            continue
        assignments.append(f"{column_name} = %s")
        values.append(value)

    if not assignments:
        return None

    assignments.extend(
        [
            "user_modified = TRUE",
            "updated_at = CURRENT_TIMESTAMP",
        ]
    )
    values.extend([component_id, analysis_run_id])

    query = f"""
        UPDATE project_components
        SET {', '.join(assignments)}
        WHERE id = %s AND analysis_run_id = %s
        RETURNING *;
    """

    with get_database_connection() as connection:
        return connection.execute(query, tuple(values)).fetchone()


def confirm_analysis(
    *,
    project_id: int,
    analysis_run_id: int,
    user_id: int,
) -> bool:
    with get_database_connection() as connection:
        row = connection.execute(
            """
                UPDATE project_analysis_runs
                SET status = 'confirmed',
                    current_step = 'confirmed',
                    confirmed_by = %s,
                    confirmed_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND project_id = %s
                  AND status = 'completed'
                RETURNING id;
            """,
            (user_id, analysis_run_id, project_id),
        ).fetchone()

        if row is None:
            return False

        connection.execute(
            """
                UPDATE projects
                SET analysis_status = 'confirmed',
                    analysis_confirmed_at = CURRENT_TIMESTAMP,
                    latest_analysis_run_id = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s;
            """,
            (analysis_run_id, project_id),
        )

        connection.execute(
            """
                INSERT INTO project_activity_logs (
                    project_id,
                    user_id,
                    action,
                    details
                )
                VALUES (%s, %s, 'analysis.confirmed', %s::JSONB);
            """,
            (
                project_id,
                user_id,
                json.dumps({"analysisRunId": analysis_run_id}),
            ),
        )

        return True
