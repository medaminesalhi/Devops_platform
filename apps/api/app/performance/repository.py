from __future__ import annotations

import json
from typing import Any

from app.database import get_database_connection


RUN_SELECT = """
    SELECT
        run.*,
        project.name AS project_name
    FROM performance_runs AS run
    INNER JOIN projects AS project
        ON project.id = run.project_id
"""


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def find_project(project_id: int) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        return connection.execute(
            """
                SELECT id, name, created_by
                FROM projects
                WHERE id = %s
                  AND archived_at IS NULL
                LIMIT 1;
            """,
            (project_id,),
        ).fetchone()


def find_deployment(deployment_id: int) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        return connection.execute(
            """
                SELECT
                    id,
                    project_id,
                    environment_id,
                    status,
                    version
                FROM deployments
                WHERE id = %s
                LIMIT 1;
            """,
            (deployment_id,),
        ).fetchone()


def create_test_and_run(
    *,
    project_id: int,
    deployment_id: int | None,
    created_by: int,
    name: str,
    description: str | None,
    target_url: str,
    test_type: str,
    mode: str,
    load_profile: dict[str, Any],
    thresholds: dict[str, Any],
    observability: dict[str, Any] | None,
) -> dict[str, Any]:
    with get_database_connection() as connection:
        test = connection.execute(
            """
                INSERT INTO performance_tests (
                    project_id,
                    deployment_id,
                    created_by,
                    name,
                    description,
                    target_url,
                    test_type,
                    mode,
                    load_profile,
                    thresholds,
                    observability
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::JSONB, %s::JSONB, %s::JSONB
                )
                RETURNING id;
            """,
            (
                project_id,
                deployment_id,
                created_by,
                name,
                description,
                target_url,
                test_type,
                mode,
                _json_dump(load_profile),
                _json_dump(thresholds),
                _json_dump(observability) if observability is not None else None,
            ),
        ).fetchone()

        if test is None:
            raise RuntimeError("Impossible de créer le test de performance.")

        run = connection.execute(
            """
                INSERT INTO performance_runs (
                    test_id,
                    project_id,
                    deployment_id,
                    created_by,
                    test_name,
                    target_url,
                    test_type,
                    mode,
                    load_profile,
                    thresholds,
                    observability,
                    status
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::JSONB, %s::JSONB, %s::JSONB, 'queued'
                )
                RETURNING id;
            """,
            (
                int(test["id"]),
                project_id,
                deployment_id,
                created_by,
                name,
                target_url,
                test_type,
                mode,
                _json_dump(load_profile),
                _json_dump(thresholds),
                _json_dump(observability) if observability is not None else None,
            ),
        ).fetchone()

        if run is None:
            raise RuntimeError("Impossible de créer le run de performance.")

        run_id = int(run["id"])
        message = (
            f"Run créé depuis le déploiement #{deployment_id}."
            if deployment_id is not None
            else "Run créé et ajouté à la file du worker k6."
        )
        connection.execute(
            """
                INSERT INTO performance_run_logs (run_id, level, message)
                VALUES (%s, 'info', %s);
            """,
            (run_id, message),
        )

    result = find_run(run_id)
    if result is None:
        raise RuntimeError("Le run créé est introuvable.")
    return result


def create_rerun_from_existing(
    *,
    source_run: dict[str, Any],
    created_by: int,
) -> dict[str, Any]:
    """Create a new queued run from an existing run snapshot."""
    with get_database_connection() as connection:
        run = connection.execute(
            """
                INSERT INTO performance_runs (
                    test_id,
                    project_id,
                    deployment_id,
                    created_by,
                    test_name,
                    target_url,
                    test_type,
                    mode,
                    load_profile,
                    thresholds,
                    observability,
                    status
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::JSONB, %s::JSONB, %s::JSONB, 'queued'
                )
                RETURNING id;
            """,
            (
                int(source_run["test_id"]),
                int(source_run["project_id"]),
                (
                    int(source_run["deployment_id"])
                    if source_run.get("deployment_id") is not None
                    else None
                ),
                created_by,
                source_run.get("test_name") or "Test de performance",
                source_run.get("target_url") or "",
                source_run.get("test_type") or "smoke",
                source_run.get("mode") or "basic",
                _json_dump(source_run.get("load_profile") or {}),
                _json_dump(source_run.get("thresholds") or {}),
                (
                    _json_dump(source_run.get("observability"))
                    if source_run.get("observability") is not None
                    else None
                ),
            ),
        ).fetchone()

        if run is None:
            raise RuntimeError("Impossible de relancer le test de performance.")

        new_run_id = int(run["id"])
        connection.execute(
            """
                INSERT INTO performance_run_logs (run_id, level, message)
                VALUES (%s, 'info', %s);
            """,
            (
                new_run_id,
                f"Run relancé depuis le run #{int(source_run['id'])} et ajouté à la file du worker k6.",
            ),
        )

    result = find_run(new_run_id)
    if result is None:
        raise RuntimeError("Le run relancé est introuvable.")
    return result


def get_overview(owner_user_id: int | None = None) -> dict[str, int]:
    conditions = []
    parameters: list[Any] = []

    if owner_user_id is not None:
        conditions.append("project.created_by = %s")
        parameters.append(owner_user_id)

    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)

    with get_database_connection() as connection:
        tests_row = connection.execute(
            f"""
                SELECT COUNT(*)::INTEGER AS count
                FROM performance_tests AS test
                INNER JOIN projects AS project
                    ON project.id = test.project_id
                {where}
                  {'AND' if where else 'WHERE'} test.is_active = TRUE;
            """,
            tuple(parameters),
        ).fetchone()

        runs_row = connection.execute(
            f"""
                SELECT
                    COUNT(*)::INTEGER AS total_runs,
                    COUNT(*) FILTER (
                        WHERE run.status IN ('queued', 'running')
                    )::INTEGER AS running_runs,
                    COUNT(*) FILTER (
                        WHERE run.status = 'passed'
                    )::INTEGER AS passed_runs,
                    COUNT(*) FILTER (
                        WHERE run.status = 'failed'
                    )::INTEGER AS failed_runs
                FROM performance_runs AS run
                INNER JOIN projects AS project
                    ON project.id = run.project_id
                {where};
            """,
            tuple(parameters),
        ).fetchone()

    return {
        "total_tests": int((tests_row or {}).get("count") or 0),
        "total_runs": int((runs_row or {}).get("total_runs") or 0),
        "running_runs": int((runs_row or {}).get("running_runs") or 0),
        "passed_runs": int((runs_row or {}).get("passed_runs") or 0),
        "failed_runs": int((runs_row or {}).get("failed_runs") or 0),
    }


def list_tests(
    *,
    owner_user_id: int | None = None,
    search: str | None = None,
    mode: str | None = None,
) -> list[dict[str, Any]]:
    conditions = ["test.is_active = TRUE"]
    parameters: list[Any] = []

    if owner_user_id is not None:
        conditions.append("project.created_by = %s")
        parameters.append(owner_user_id)

    if search:
        conditions.append(
            """
                (
                    test.name ILIKE '%%' || %s || '%%'
                    OR project.name ILIKE '%%' || %s || '%%'
                    OR test.target_url ILIKE '%%' || %s || '%%'
                )
            """
        )
        parameters.extend([search, search, search])

    if mode:
        conditions.append("test.mode = %s")
        parameters.append(mode)

    query = f"""
        SELECT
            test.*,
            project.name AS project_name,
            last_run.id AS last_run_id,
            last_run.status AS last_run_status,
            last_run.created_at AS last_run_created_at,
            last_run.started_at AS last_run_started_at,
            last_run.finished_at AS last_run_finished_at,
            last_run.metrics AS last_run_metrics,
            last_run.grafana_dashboard_url AS last_run_grafana_dashboard_url
        FROM performance_tests AS test
        INNER JOIN projects AS project
            ON project.id = test.project_id
        LEFT JOIN LATERAL (
            SELECT run.*
            FROM performance_runs AS run
            WHERE run.test_id = test.id
            ORDER BY run.created_at DESC, run.id DESC
            LIMIT 1
        ) AS last_run ON TRUE
        WHERE {' AND '.join(conditions)}
        ORDER BY test.updated_at DESC, test.id DESC;
    """

    with get_database_connection() as connection:
        return connection.execute(query, tuple(parameters)).fetchall()


def list_runs(
    *,
    owner_user_id: int | None = None,
    status: str | None = None,
    mode: str | None = None,
) -> list[dict[str, Any]]:
    conditions = ["1 = 1"]
    parameters: list[Any] = []

    if owner_user_id is not None:
        conditions.append("project.created_by = %s")
        parameters.append(owner_user_id)

    if status:
        conditions.append("run.status = %s")
        parameters.append(status)

    if mode:
        conditions.append("run.mode = %s")
        parameters.append(mode)

    query = f"""
        {RUN_SELECT}
        WHERE {' AND '.join(conditions)}
        ORDER BY run.created_at DESC, run.id DESC;
    """

    with get_database_connection() as connection:
        return connection.execute(query, tuple(parameters)).fetchall()


def find_run(
    run_id: int,
    *,
    owner_user_id: int | None = None,
) -> dict[str, Any] | None:
    conditions = ["run.id = %s"]
    parameters: list[Any] = [run_id]

    if owner_user_id is not None:
        conditions.append("project.created_by = %s")
        parameters.append(owner_user_id)

    query = f"""
        {RUN_SELECT}
        WHERE {' AND '.join(conditions)}
        LIMIT 1;
    """

    with get_database_connection() as connection:
        return connection.execute(query, tuple(parameters)).fetchone()


def list_run_logs(run_id: int) -> list[dict[str, Any]]:
    with get_database_connection() as connection:
        return connection.execute(
            """
                SELECT id, run_id, level, message, created_at
                FROM performance_run_logs
                WHERE run_id = %s
                ORDER BY created_at, id;
            """,
            (run_id,),
        ).fetchall()


def list_run_samples(run_id: int) -> list[dict[str, Any]]:
    with get_database_connection() as connection:
        return connection.execute(
            """
                SELECT
                    id,
                    run_id,
                    sampled_at,
                    elapsed_seconds,
                    vus,
                    requests,
                    requests_total,
                    iterations_total,
                    rps,
                    avg_ms,
                    p95_ms,
                    p99_ms,
                    error_rate_percent,
                    checks_rate_percent
                FROM performance_run_samples
                WHERE run_id = %s
                ORDER BY elapsed_seconds, id;
            """,
            (run_id,),
        ).fetchall()


def replace_run_samples(run_id: int, samples: list[dict[str, Any]]) -> None:
    with get_database_connection() as connection:
        connection.execute(
            "DELETE FROM performance_run_samples WHERE run_id = %s;",
            (run_id,),
        )

        if not samples:
            return

        rows = [
            (
                run_id,
                sample.get("sampledAt"),
                int(sample.get("elapsedSeconds") or 0),
                int(sample.get("vus") or 0),
                int(sample.get("requests") or 0),
                int(sample.get("requestsTotal") or 0),
                int(sample.get("iterationsTotal") or 0),
                float(sample.get("rps") or 0.0),
                float(sample.get("avgMs") or 0.0),
                float(sample.get("p95Ms") or 0.0),
                float(sample.get("p99Ms") or 0.0),
                float(sample.get("errorRatePercent") or 0.0),
                float(sample.get("checksRatePercent") or 0.0),
            )
            for sample in samples
        ]

        with connection.cursor() as cursor:
            cursor.executemany(
                """
                    INSERT INTO performance_run_samples (
                        run_id,
                        sampled_at,
                        elapsed_seconds,
                        vus,
                        requests,
                        requests_total,
                        iterations_total,
                        rps,
                        avg_ms,
                        p95_ms,
                        p99_ms,
                        error_rate_percent,
                        checks_rate_percent
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s
                    );
                """,
                rows,
            )


def add_log(
    run_id: int,
    *,
    level: str,
    message: str,
) -> dict[str, Any]:
    with get_database_connection() as connection:
        row = connection.execute(
            """
                INSERT INTO performance_run_logs (run_id, level, message)
                VALUES (%s, %s, %s)
                RETURNING id, run_id, level, message, created_at;
            """,
            (run_id, level, message),
        ).fetchone()

    if row is None:
        raise RuntimeError("Impossible d'enregistrer le log k6.")
    return row


def request_cancellation(run_id: int) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        row = connection.execute(
            """
                UPDATE performance_runs
                SET cancel_requested = TRUE
                WHERE id = %s
                  AND status IN ('queued', 'running')
                RETURNING id, status;
            """,
            (run_id,),
        ).fetchone()

        if row is None:
            return None

        if row["status"] == "queued":
            connection.execute(
                """
                    UPDATE performance_runs
                    SET status = 'cancelled',
                        finished_at = CURRENT_TIMESTAMP,
                        error_code = 'CANCELLED',
                        error_message = 'Exécution annulée avant le démarrage.',
                        locked_at = NULL,
                        worker_name = NULL,
                        heartbeat_at = NULL
                    WHERE id = %s;
                """,
                (run_id,),
            )

    return find_run(run_id)


def run_cancel_requested(run_id: int) -> bool:
    with get_database_connection() as connection:
        row = connection.execute(
            """
                SELECT cancel_requested
                FROM performance_runs
                WHERE id = %s;
            """,
            (run_id,),
        ).fetchone()
    return bool(row and row["cancel_requested"])


def claim_next_run(worker_name: str) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        row = connection.execute(
            """
                SELECT id
                FROM performance_runs
                WHERE status = 'queued'
                  AND cancel_requested = FALSE
                  AND locked_at IS NULL
                ORDER BY created_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1;
            """
        ).fetchone()

        if row is None:
            return None

        run_id = int(row["id"])
        connection.execute(
            """
                UPDATE performance_runs
                SET status = 'running',
                    started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                    locked_at = CURRENT_TIMESTAMP,
                    heartbeat_at = CURRENT_TIMESTAMP,
                    worker_name = %s,
                    error_code = NULL,
                    error_message = NULL
                WHERE id = %s;
            """,
            (worker_name, run_id),
        )

    return find_run(run_id)


def claim_run_by_id(run_id: int, worker_name: str) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        row = connection.execute(
            """
                UPDATE performance_runs
                SET status = 'running',
                    started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                    locked_at = CURRENT_TIMESTAMP,
                    heartbeat_at = CURRENT_TIMESTAMP,
                    worker_name = %s,
                    error_code = NULL,
                    error_message = NULL
                WHERE id = %s
                  AND status = 'queued'
                  AND cancel_requested = FALSE
                RETURNING id;
            """,
            (worker_name, run_id),
        ).fetchone()

    if row is None:
        return None
    return find_run(run_id)


def heartbeat(run_id: int, worker_name: str) -> None:
    with get_database_connection() as connection:
        connection.execute(
            """
                UPDATE performance_runs
                SET heartbeat_at = CURRENT_TIMESTAMP,
                    locked_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND status = 'running'
                  AND worker_name = %s;
            """,
            (run_id, worker_name),
        )


def finish_run(
    run_id: int,
    *,
    status: str,
    exit_code: int | None,
    metrics: dict[str, Any] | None,
    threshold_results: list[dict[str, Any]] | None,
    summary: dict[str, Any] | None,
    grafana_dashboard_url: str | None,
    error_code: str | None,
    error_message: str | None,
) -> None:
    with get_database_connection() as connection:
        connection.execute(
            """
                UPDATE performance_runs
                SET status = %s,
                    finished_at = CURRENT_TIMESTAMP,
                    exit_code = %s,
                    metrics = %s::JSONB,
                    threshold_results = %s::JSONB,
                    summary = %s::JSONB,
                    grafana_dashboard_url = %s,
                    error_code = %s,
                    error_message = %s,
                    locked_at = NULL,
                    heartbeat_at = NULL,
                    worker_name = NULL
                WHERE id = %s;
            """,
            (
                status,
                exit_code,
                _json_dump(metrics) if metrics is not None else None,
                _json_dump(threshold_results) if threshold_results is not None else None,
                _json_dump(summary) if summary is not None else None,
                grafana_dashboard_url,
                error_code,
                error_message,
                run_id,
            ),
        )


def fail_stale_runs(stale_seconds: int) -> int:
    with get_database_connection() as connection:
        rows = connection.execute(
            """
                UPDATE performance_runs
                SET status = 'failed',
                    finished_at = CURRENT_TIMESTAMP,
                    error_code = 'WORKER_HEARTBEAT_LOST',
                    error_message = 'Le worker k6 ne donne plus de signe de vie.',
                    locked_at = NULL,
                    heartbeat_at = NULL,
                    worker_name = NULL
                WHERE status = 'running'
                  AND heartbeat_at IS NOT NULL
                  AND heartbeat_at < (
                      CURRENT_TIMESTAMP - make_interval(secs => %s)
                  )
                RETURNING id;
            """,
            (stale_seconds,),
        ).fetchall()

        for row in rows:
            connection.execute(
                """
                    INSERT INTO performance_run_logs (run_id, level, message)
                    VALUES (
                        %s,
                        'error',
                        'Le run a été marqué en échec car le heartbeat du worker a expiré.'
                    );
                """,
                (int(row["id"]),),
            )

    return len(rows)
