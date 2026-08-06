from __future__ import annotations

import json
from typing import Any, Iterable

from app.database import get_database_connection


DEPLOYMENT_SELECT = """
    SELECT
        deployment.*,

        project.name AS project_name,
        project.slug AS project_slug,

        environment.name AS environment_name,
        environment.code AS environment_code,
        environment.namespace,
        environment.domain AS environment_domain,

        generation.analysis_run_id,
        analysis.analyzed_commit_sha AS source_commit,

        COALESCE(
            NULLIF(
                BTRIM(
                    CONCAT_WS(
                        ' ',
                        platform_user.first_name,
                        platform_user.last_name
                    )
                ),
                ''
            ),
            platform_user.username,
            'Système'
        ) AS created_by_name

    FROM deployments AS deployment

    INNER JOIN projects AS project
        ON project.id = deployment.project_id

    LEFT JOIN deployment_environments AS environment
        ON environment.id = deployment.environment_id

    LEFT JOIN project_generation_runs AS generation
        ON generation.id = deployment.generation_run_id

    LEFT JOIN project_analysis_runs AS analysis
        ON analysis.id = generation.analysis_run_id

    LEFT JOIN users AS platform_user
        ON platform_user.id = deployment.triggered_by
"""


DEFAULT_STEPS: tuple[tuple[str, str, str, str], ...] = (
    (
        "prepare",
        "prepare",
        "Préparer le workspace",
        "Créer un espace temporaire sécurisé et charger la release.",
    ),
    (
        "source",
        "source",
        "Récupérer le commit approuvé",
        "Charger exactement la version confirmée pendant l’analyse.",
    ),
    (
        "build",
        "build",
        "Construire les images",
        "Exécuter les builds Docker contrôlés pour chaque composant.",
    ),
    (
        "registry",
        "registry",
        "Publier vers Nexus",
        "Pousser les images puis enregistrer leurs digests.",
    ),
    (
        "gitops",
        "gitops",
        "Publier dans GitOps",
        "Versionner les charts Helm et les références d’images.",
    ),
    (
        "argocd",
        "argocd",
        "Synchroniser Argo CD",
        "Créer ou mettre à jour les objets Argo CD puis synchroniser.",
    ),
    (
        "kubernetes",
        "kubernetes",
        "Observer Kubernetes",
        "Attendre les pods, services, jobs et ingress attendus.",
    ),
    (
        "health",
        "health",
        "Vérifier la santé",
        "Contrôler Argo CD, les probes, les pods et l’accès applicatif.",
    ),
)


def _json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return fallback


def table_columns(database_connection, table_name: str) -> set[str]:
    rows = database_connection.execute(
        """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s;
        """,
        (table_name,),
    ).fetchall()
    return {str(row["column_name"]) for row in rows}


def list_deployments(
    *,
    search: str | None = None,
    project_id: int | None = None,
    environment_id: int | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    conditions = ["1 = 1"]
    parameters: list[Any] = []

    if search:
        conditions.append(
            """
                (
                    project.name ILIKE '%%' || %s || '%%'
                    OR deployment.version ILIKE '%%' || %s || '%%'
                    OR deployment.commit_sha ILIKE '%%' || %s || '%%'
                )
            """
        )
        parameters.extend([search, search, search])

    if project_id is not None:
        conditions.append("deployment.project_id = %s")
        parameters.append(project_id)

    if environment_id is not None:
        conditions.append("deployment.environment_id = %s")
        parameters.append(environment_id)

    if status:
        conditions.append("deployment.status = %s")
        parameters.append(status)

    if date_from:
        conditions.append("deployment.created_at::DATE >= %s::DATE")
        parameters.append(date_from)

    if date_to:
        conditions.append("deployment.created_at::DATE <= %s::DATE")
        parameters.append(date_to)

    query = f"""
        {DEPLOYMENT_SELECT}
        WHERE {' AND '.join(conditions)}
        ORDER BY deployment.created_at DESC, deployment.id DESC;
    """

    with get_database_connection() as connection:
        return connection.execute(query, tuple(parameters)).fetchall()


def find_deployment(deployment_id: int) -> dict[str, Any] | None:
    query = f"""
        {DEPLOYMENT_SELECT}
        WHERE deployment.id = %s
        LIMIT 1;
    """
    with get_database_connection() as connection:
        return connection.execute(query, (deployment_id,)).fetchone()


def list_deployment_steps(deployment_id: int) -> list[dict[str, Any]]:
    with get_database_connection() as connection:
        return connection.execute(
            """
                SELECT
                    id,
                    deployment_id,
                    step_order,
                    code,
                    stage,
                    name,
                    description,
                    status,
                    details,
                    error_code,
                    error_message,
                    duration_seconds,
                    started_at,
                    finished_at,
                    created_at
                FROM deployment_steps
                WHERE deployment_id = %s
                ORDER BY step_order;
            """,
            (deployment_id,),
        ).fetchall()


def find_step_by_stage(
    deployment_id: int,
    stage: str,
) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        return connection.execute(
            """
                SELECT *
                FROM deployment_steps
                WHERE deployment_id = %s
                  AND stage = %s
                LIMIT 1;
            """,
            (deployment_id, stage),
        ).fetchone()


def list_deployment_logs(
    deployment_id: int,
    *,
    after_id: int = 0,
    limit: int = 4000,
) -> list[dict[str, Any]]:
    with get_database_connection() as connection:
        return connection.execute(
            """
                SELECT
                    log.id,
                    log.deployment_id,
                    log.step_id,
                    step.code AS step_code,
                    log.scope,
                    log.level,
                    log.component_name,
                    log.message,
                    log.created_at
                FROM deployment_logs AS log
                LEFT JOIN deployment_steps AS step
                  ON step.id = log.step_id
                WHERE log.deployment_id = %s
                  AND log.id > %s
                ORDER BY log.id
                LIMIT %s;
            """,
            (deployment_id, after_id, limit),
        ).fetchall()


def list_deployment_components(deployment_id: int) -> list[dict[str, Any]]:
    with get_database_connection() as connection:
        return connection.execute(
            """
                SELECT *
                FROM deployment_components
                WHERE deployment_id = %s
                ORDER BY id;
            """,
            (deployment_id,),
        ).fetchall()


def list_deployment_resources(deployment_id: int) -> list[dict[str, Any]]:
    with get_database_connection() as connection:
        return connection.execute(
            """
                SELECT *
                FROM deployment_resources
                WHERE deployment_id = %s
                ORDER BY kind, name;
            """,
            (deployment_id,),
        ).fetchall()


def find_current_incident(deployment_id: int) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        return connection.execute(
            """
                SELECT
                    incident.*,
                    step.code AS step_code
                FROM deployment_incidents AS incident
                LEFT JOIN deployment_steps AS step
                  ON step.id = incident.step_id
                WHERE incident.deployment_id = %s
                  AND incident.resolved_at IS NULL
                ORDER BY incident.occurred_at DESC, incident.id DESC
                LIMIT 1;
            """,
            (deployment_id,),
        ).fetchone()


def find_diagnostic(deployment_id: int) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        return connection.execute(
            """
                SELECT *
                FROM deployment_diagnostics
                WHERE deployment_id = %s
                LIMIT 1;
            """,
            (deployment_id,),
        ).fetchone()


def list_chat_messages(deployment_id: int) -> list[dict[str, Any]]:
    with get_database_connection() as connection:
        return connection.execute(
            """
                SELECT id, role, content, created_by, created_at
                FROM deployment_chat_messages
                WHERE deployment_id = %s
                ORDER BY id;
            """,
            (deployment_id,),
        ).fetchall()


def list_corrections(deployment_id: int) -> list[dict[str, Any]]:
    with get_database_connection() as connection:
        return connection.execute(
            """
                SELECT *
                FROM deployment_corrections
                WHERE deployment_id = %s
                ORDER BY id;
            """,
            (deployment_id,),
        ).fetchall()


def list_generation_options() -> list[dict[str, Any]]:
    with get_database_connection() as connection:
        return connection.execute(
            """
                SELECT
                    project.id AS project_id,
                    project.name AS project_name,
                    project.slug AS project_slug,
                    project.default_environment_id AS environment_id,
                    environment.name AS environment_name,
                    environment.namespace,

                    generation.id AS generation_id,
                    generation.created_at AS generation_created_at,
                    analysis.analyzed_commit_sha AS source_commit,

                    COUNT(DISTINCT component.id) AS component_count,
                    COUNT(DISTINCT artifact.id) AS artifact_count,
                    COUNT(DISTINCT artifact.id)
                        FILTER (WHERE artifact.review_status = 'approved')
                        AS approved_artifact_count,
                    COUNT(DISTINCT artifact.id)
                        FILTER (WHERE artifact.review_status <> 'approved')
                        AS unapproved_artifact_count

                FROM projects AS project

                INNER JOIN project_generation_runs AS generation
                    ON generation.project_id = project.id

                INNER JOIN project_analysis_runs AS analysis
                    ON analysis.id = generation.analysis_run_id

                LEFT JOIN project_components AS component
                    ON component.analysis_run_id = generation.analysis_run_id
                   AND component.deployable = TRUE

                LEFT JOIN project_generated_artifacts AS artifact
                    ON artifact.generation_run_id = generation.id

                LEFT JOIN deployment_environments AS environment
                    ON environment.id = project.default_environment_id

                WHERE project.archived_at IS NULL
                  AND project.status = 'active'
                  AND generation.status = 'completed'

                GROUP BY
                    project.id,
                    project.name,
                    project.slug,
                    project.default_environment_id,
                    environment.name,
                    environment.namespace,
                    generation.id,
                    generation.created_at,
                    analysis.analyzed_commit_sha

                HAVING COUNT(DISTINCT artifact.id) > 0
                   AND COUNT(DISTINCT artifact.id)
                       FILTER (WHERE artifact.review_status <> 'approved') = 0

                ORDER BY project.name, generation.created_at DESC;
            """
        ).fetchall()


def find_generation_for_deployment(
    *,
    project_id: int,
    generation_id: int,
) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        row = connection.execute(
            """
                SELECT
                    generation.*,
                    project.name AS project_name,
                    project.slug AS project_slug,
                    project.status AS project_status,
                    project.default_environment_id,
                    environment.name AS environment_name,
                    environment.code AS environment_code,
                    environment.namespace,
                    environment.domain,
                    environment.configuration_status,
                    analysis.analyzed_commit_sha,
                    analysis.status AS analysis_status,
                    COUNT(artifact.id) AS artifact_count,
                    COUNT(artifact.id)
                        FILTER (WHERE artifact.review_status <> 'approved')
                        AS unapproved_artifact_count
                FROM project_generation_runs AS generation
                INNER JOIN projects AS project
                  ON project.id = generation.project_id
                INNER JOIN project_analysis_runs AS analysis
                  ON analysis.id = generation.analysis_run_id
                LEFT JOIN deployment_environments AS environment
                  ON environment.id = project.default_environment_id
                LEFT JOIN project_generated_artifacts AS artifact
                  ON artifact.generation_run_id = generation.id
                WHERE generation.id = %s
                  AND generation.project_id = %s
                  AND project.archived_at IS NULL
                GROUP BY
                    generation.id,
                    project.id,
                    environment.id,
                    analysis.id
                LIMIT 1;
            """,
            (generation_id, project_id),
        ).fetchone()
        return row


def find_latest_ready_generation(project_id: int) -> dict[str, Any] | None:
    rows = [
        row
        for row in list_generation_options()
        if int(row["project_id"]) == project_id
    ]
    return rows[0] if rows else None


def find_project_for_deployment(project_id: int) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        return connection.execute(
            """
                SELECT
                    project.*,
                    environment.name AS environment_name,
                    environment.code AS environment_code,
                    environment.namespace,
                    environment.domain,
                    environment.configuration_status
                FROM projects AS project
                LEFT JOIN deployment_environments AS environment
                  ON environment.id = project.default_environment_id
                WHERE project.id = %s
                  AND project.archived_at IS NULL
                LIMIT 1;
            """,
            (project_id,),
        ).fetchone()


def list_environment_connections(environment_id: int) -> list[dict[str, Any]]:
    with get_database_connection() as connection:
        return connection.execute(
            """
                SELECT
                    link.service_role,
                    link.is_required,
                    service.id,
                    service.name,
                    service.provider_type,
                    service.base_url,
                    service.environment,
                    service.description,
                    service.enabled,
                    service.verify_ssl,
                    service.status,
                    service.last_error,
                    credential.auth_type,
                    credential.username,
                    credential.secret_ciphertext,
                    (credential.secret_ciphertext IS NOT NULL)
                        AS credential_configured
                FROM environment_connections AS link
                INNER JOIN integration_connections AS service
                  ON service.id = link.connection_id
                LEFT JOIN integration_credentials AS credential
                  ON credential.connection_id = service.id
                WHERE link.environment_id = %s
                ORDER BY link.service_role;
            """,
            (environment_id,),
        ).fetchall()


def find_environment_connection(
    *,
    environment_id: int,
    service_role: str,
) -> dict[str, Any] | None:
    rows = list_environment_connections(environment_id)
    return next(
        (row for row in rows if row["service_role"] == service_role),
        None,
    )


def find_confirmed_contract(project_id: int) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        columns = table_columns(connection, "project_deployment_contracts")
        if not columns:
            return None

        contract_column = (
            "contract"
            if "contract" in columns
            else "contract_json"
            if "contract_json" in columns
            else None
        )
        if contract_column is None:
            return None

        order = (
            "confirmed_at DESC NULLS LAST, created_at DESC"
            if "confirmed_at" in columns
            else "created_at DESC"
        )

        row = connection.execute(
            f"""
                SELECT *, {contract_column} AS deployment_contract
                FROM project_deployment_contracts
                WHERE project_id = %s
                  AND status = 'confirmed'
                ORDER BY {order}
                LIMIT 1;
            """,
            (project_id,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["deployment_contract"] = _json(
            result.get("deployment_contract"),
            {},
        )
        return result


def list_generation_artifacts(generation_id: int) -> list[dict[str, Any]]:
    with get_database_connection() as connection:
        return connection.execute(
            """
                SELECT
                    artifact.*,
                    component.name AS component_name,
                    component.root_path AS component_root_path,
                    component.component_type
                FROM project_generated_artifacts AS artifact
                LEFT JOIN project_components AS component
                  ON component.id = artifact.component_id
                WHERE artifact.generation_run_id = %s
                ORDER BY artifact.relative_path;
            """,
            (generation_id,),
        ).fetchall()


def list_generation_components(generation_id: int) -> list[dict[str, Any]]:
    with get_database_connection() as connection:
        return connection.execute(
            """
                SELECT component.*
                FROM project_generation_runs AS generation
                INNER JOIN project_components AS component
                  ON component.analysis_run_id = generation.analysis_run_id
                WHERE generation.id = %s
                  AND component.deployable = TRUE
                ORDER BY component.root_path, component.name;
            """,
            (generation_id,),
        ).fetchall()


def create_deployment(
    *,
    project_id: int,
    generation_id: int,
    environment_id: int,
    environment_name: str,
    source_commit: str,
    version: str,
    note: str | None,
    sync_mode: str,
    user_id: int,
    components: list[dict[str, Any]],
) -> dict[str, Any]:
    with get_database_connection() as connection:
        deployment = connection.execute(
            """
                INSERT INTO deployments (
                    project_id,
                    environment,
                    environment_id,
                    generation_run_id,
                    status,
                    commit_sha,
                    image_tag,
                    version,
                    sync_mode,
                    current_stage,
                    current_stage_label,
                    progress,
                    note,
                    triggered_by
                )
                VALUES (
                    %s, %s, %s, %s,
                    'ready',
                    %s, %s, %s, %s,
                    'prepare',
                    'Prêt à démarrer',
                    0,
                    %s,
                    %s
                )
                RETURNING *;
            """,
            (
                project_id,
                environment_name,
                environment_id,
                generation_id,
                source_commit,
                version,
                version,
                sync_mode,
                note,
                user_id,
            ),
        ).fetchone()

        if deployment is None:
            raise RuntimeError("Impossible de créer le déploiement.")

        deployment_id = int(deployment["id"])

        for order, (code, stage, name, description) in enumerate(
            DEFAULT_STEPS,
            start=1,
        ):
            connection.execute(
                """
                    INSERT INTO deployment_steps (
                        deployment_id,
                        step_order,
                        code,
                        stage,
                        name,
                        description,
                        status,
                        details
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, 'pending', '{}'::JSONB);
                """,
                (
                    deployment_id,
                    order,
                    code,
                    stage,
                    name,
                    description,
                ),
            )

        for component in components:
            connection.execute(
                """
                    INSERT INTO deployment_components (
                        deployment_id,
                        component_id,
                        component_key,
                        name,
                        component_type,
                        root_path,
                        dockerfile_path,
                        image_repository,
                        image_tag,
                        port,
                        replicas
                    )
                    VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s
                    );
                """,
                (
                    deployment_id,
                    component.get("component_id"),
                    component["component_key"],
                    component["name"],
                    component.get("component_type") or "application",
                    component.get("root_path") or ".",
                    component.get("dockerfile_path"),
                    component["image_repository"],
                    version,
                    component.get("port"),
                    int(component.get("replicas") or 1),
                ),
            )

        connection.execute(
            """
                INSERT INTO deployment_diagnostics (
                    deployment_id,
                    status,
                    evidence
                )
                VALUES (%s, 'idle', '[]'::JSONB)
                ON CONFLICT (deployment_id) DO NOTHING;
            """,
            (deployment_id,),
        )

        connection.execute(
            """
                INSERT INTO deployment_chat_messages (
                    deployment_id,
                    role,
                    content
                )
                VALUES (
                    %s,
                    'assistant',
                    'Je surveillerai cette exécution. En cas d’erreur, lancez le diagnostic IA pour obtenir une correction contrôlée.'
                );
            """,
            (deployment_id,),
        )

    result = find_deployment(deployment_id)
    if result is None:
        raise RuntimeError("Le déploiement créé est introuvable.")
    return result


def update_deployment(
    deployment_id: int,
    **changes: Any,
) -> dict[str, Any] | None:
    allowed = {
        "status",
        "current_stage",
        "current_stage_label",
        "progress",
        "gitops_commit",
        "sync_confirmed_at",
        "error_code",
        "error_message",
        "cancel_requested",
        "locked_at",
        "locked_by",
        "started_at",
        "finished_at",
    }
    assignments: list[str] = []
    parameters: list[Any] = []

    for name, value in changes.items():
        if name not in allowed:
            continue
        assignments.append(f"{name} = %s")
        parameters.append(value)

    if not assignments:
        return find_deployment(deployment_id)

    parameters.append(deployment_id)
    with get_database_connection() as connection:
        row = connection.execute(
            f"""
                UPDATE deployments
                SET {', '.join(assignments)}
                WHERE id = %s
                RETURNING id;
            """,
            tuple(parameters),
        ).fetchone()
        if row is None:
            return None
    return find_deployment(deployment_id)


def queue_deployment(deployment_id: int) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        row = connection.execute(
            """
                UPDATE deployments
                SET
                    status = 'queued',
                    current_stage_label = 'En attente du worker',
                    cancel_requested = FALSE,
                    error_code = NULL,
                    error_message = NULL,
                    finished_at = NULL,
                    locked_at = NULL,
                    locked_by = NULL
                WHERE id = %s
                  AND status IN ('ready', 'failed', 'waiting_confirmation')
                RETURNING id;
            """,
            (deployment_id,),
        ).fetchone()
        if row is None:
            return None
    return find_deployment(deployment_id)


def request_cancellation(deployment_id: int) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        row = connection.execute(
            """
                UPDATE deployments
                SET cancel_requested = TRUE,
                    current_stage_label = 'Annulation demandée'
                WHERE id = %s
                  AND status IN ('queued', 'running', 'waiting_confirmation')
                RETURNING id;
            """,
            (deployment_id,),
        ).fetchone()
        if row is None:
            return None
    return find_deployment(deployment_id)


def deployment_cancel_requested(deployment_id: int) -> bool:
    with get_database_connection() as connection:
        row = connection.execute(
            "SELECT cancel_requested FROM deployments WHERE id = %s;",
            (deployment_id,),
        ).fetchone()
    return bool(row and row["cancel_requested"])


def claim_next_deployment(worker_name: str) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        row = connection.execute(
            """
                SELECT id
                FROM deployments
                WHERE status = 'queued'
                  AND (
                      locked_at IS NULL
                      OR locked_at < CURRENT_TIMESTAMP - INTERVAL '30 minutes'
                  )
                ORDER BY created_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1;
            """
        ).fetchone()
        if row is None:
            return None

        deployment_id = int(row["id"])
        connection.execute(
            """
                UPDATE deployments
                SET status = 'running',
                    started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                    locked_at = CURRENT_TIMESTAMP,
                    locked_by = %s,
                    current_stage_label = 'Pris en charge par le worker'
                WHERE id = %s;
            """,
            (worker_name, deployment_id),
        )

    return find_deployment(deployment_id)


def claim_deployment_by_id(
    deployment_id: int,
    worker_name: str,
) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        row = connection.execute(
            """
                UPDATE deployments
                SET status = 'running',
                    started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                    locked_at = CURRENT_TIMESTAMP,
                    locked_by = %s
                WHERE id = %s
                  AND status IN ('queued', 'running')
                RETURNING id;
            """,
            (worker_name, deployment_id),
        ).fetchone()
        if row is None:
            return None
    return find_deployment(deployment_id)


def release_deployment_lock(deployment_id: int) -> None:
    with get_database_connection() as connection:
        connection.execute(
            """
                UPDATE deployments
                SET locked_at = NULL,
                    locked_by = NULL
                WHERE id = %s;
            """,
            (deployment_id,),
        )


def update_step(
    *,
    deployment_id: int,
    stage: str,
    status: str,
    details: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        row = connection.execute(
            """
                UPDATE deployment_steps
                SET
                    status = %s,
                    details = COALESCE(%s::JSONB, details),
                    error_code = %s,
                    error_message = %s,
                    started_at = CASE
                        WHEN %s = 'running'
                        THEN COALESCE(started_at, CURRENT_TIMESTAMP)
                        ELSE started_at
                    END,
                    finished_at = CASE
                        WHEN %s IN ('succeeded', 'failed', 'skipped', 'cancelled')
                        THEN CURRENT_TIMESTAMP
                        ELSE NULL
                    END,
                    duration_seconds = CASE
                        WHEN %s IN ('succeeded', 'failed', 'skipped', 'cancelled')
                             AND started_at IS NOT NULL
                        THEN GREATEST(
                            0,
                            EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - started_at))::INTEGER
                        )
                        ELSE duration_seconds
                    END
                WHERE deployment_id = %s
                  AND stage = %s
                RETURNING *;
            """,
            (
                status,
                json.dumps(details) if details is not None else None,
                error_code,
                error_message,
                status,
                status,
                status,
                deployment_id,
                stage,
            ),
        ).fetchone()
        return row


def reset_steps_from_stage(deployment_id: int, stage: str) -> None:
    with get_database_connection() as connection:
        step = connection.execute(
            """
                SELECT step_order
                FROM deployment_steps
                WHERE deployment_id = %s AND stage = %s
                LIMIT 1;
            """,
            (deployment_id, stage),
        ).fetchone()
        if step is None:
            return
        connection.execute(
            """
                UPDATE deployment_steps
                SET status = 'pending',
                    error_code = NULL,
                    error_message = NULL,
                    started_at = NULL,
                    finished_at = NULL,
                    duration_seconds = NULL,
                    details = '{}'::JSONB
                WHERE deployment_id = %s
                  AND step_order >= %s;
            """,
            (deployment_id, step["step_order"]),
        )


def skip_steps(deployment_id: int, stages: Iterable[str]) -> None:
    stages_tuple = tuple(stages)
    if not stages_tuple:
        return
    with get_database_connection() as connection:
        connection.execute(
            """
                UPDATE deployment_steps
                SET status = 'skipped',
                    finished_at = CURRENT_TIMESTAMP,
                    details = jsonb_build_object('reason', 'prepare_only')
                WHERE deployment_id = %s
                  AND stage = ANY(%s::TEXT[])
                  AND status = 'pending';
            """,
            (deployment_id, list(stages_tuple)),
        )


def add_log(
    *,
    deployment_id: int,
    scope: str,
    level: str,
    message: str,
    stage: str | None = None,
    component_name: str | None = None,
) -> dict[str, Any]:
    with get_database_connection() as connection:
        step_id = None
        if stage:
            step = connection.execute(
                """
                    SELECT id
                    FROM deployment_steps
                    WHERE deployment_id = %s AND stage = %s
                    LIMIT 1;
                """,
                (deployment_id, stage),
            ).fetchone()
            if step:
                step_id = step["id"]

        row = connection.execute(
            """
                INSERT INTO deployment_logs (
                    deployment_id,
                    step_id,
                    scope,
                    level,
                    component_name,
                    message
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING *;
            """,
            (
                deployment_id,
                step_id,
                scope,
                level,
                component_name,
                message,
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("Impossible d’enregistrer le log.")
        return row


def update_component_status(
    *,
    deployment_id: int,
    component_key: str,
    build_status: str | None = None,
    registry_status: str | None = None,
    image_digest: str | None = None,
) -> None:
    assignments: list[str] = []
    parameters: list[Any] = []
    if build_status is not None:
        assignments.append("build_status = %s")
        parameters.append(build_status)
    if registry_status is not None:
        assignments.append("registry_status = %s")
        parameters.append(registry_status)
    if image_digest is not None:
        assignments.append("image_digest = %s")
        parameters.append(image_digest)
    if not assignments:
        return
    parameters.extend([deployment_id, component_key])
    with get_database_connection() as connection:
        connection.execute(
            f"""
                UPDATE deployment_components
                SET {', '.join(assignments)}
                WHERE deployment_id = %s
                  AND component_key = %s;
            """,
            tuple(parameters),
        )


def replace_resources(
    deployment_id: int,
    resources: list[dict[str, Any]],
) -> None:
    with get_database_connection() as connection:
        for resource in resources:
            connection.execute(
                """
                    INSERT INTO deployment_resources (
                        deployment_id,
                        resource_key,
                        kind,
                        name,
                        namespace,
                        status,
                        health,
                        ready,
                        image,
                        restarts,
                        age,
                        message,
                        url,
                        raw,
                        observed_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s::JSONB,
                        CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (deployment_id, resource_key)
                    DO UPDATE SET
                        status = EXCLUDED.status,
                        health = EXCLUDED.health,
                        ready = EXCLUDED.ready,
                        image = EXCLUDED.image,
                        restarts = EXCLUDED.restarts,
                        age = EXCLUDED.age,
                        message = EXCLUDED.message,
                        url = EXCLUDED.url,
                        raw = EXCLUDED.raw,
                        observed_at = CURRENT_TIMESTAMP;
                """,
                (
                    deployment_id,
                    resource["resource_key"],
                    resource["kind"],
                    resource["name"],
                    resource["namespace"],
                    resource.get("status") or "Unknown",
                    resource.get("health") or "unknown",
                    resource.get("ready"),
                    resource.get("image"),
                    resource.get("restarts"),
                    resource.get("age") or "—",
                    resource.get("message"),
                    resource.get("url"),
                    json.dumps(resource.get("raw") or {}),
                ),
            )


def create_incident(
    *,
    deployment_id: int,
    stage: str,
    code: str,
    title: str,
    message: str,
    component_name: str | None,
    integration_name: str | None,
    retryable: bool,
    requires_new_generation: bool,
) -> dict[str, Any]:
    with get_database_connection() as connection:
        connection.execute(
            """
                UPDATE deployment_incidents
                SET resolved_at = CURRENT_TIMESTAMP
                WHERE deployment_id = %s
                  AND resolved_at IS NULL;
            """,
            (deployment_id,),
        )
        step = connection.execute(
            """
                SELECT id
                FROM deployment_steps
                WHERE deployment_id = %s AND stage = %s
                LIMIT 1;
            """,
            (deployment_id, stage),
        ).fetchone()
        row = connection.execute(
            """
                INSERT INTO deployment_incidents (
                    deployment_id,
                    step_id,
                    code,
                    title,
                    message,
                    stage,
                    component_name,
                    integration_name,
                    retryable,
                    requires_new_generation
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *;
            """,
            (
                deployment_id,
                step["id"] if step else None,
                code,
                title,
                message,
                stage,
                component_name,
                integration_name,
                retryable,
                requires_new_generation,
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("Impossible d’enregistrer l’incident.")
        return row


def resolve_incidents(deployment_id: int) -> None:
    with get_database_connection() as connection:
        connection.execute(
            """
                UPDATE deployment_incidents
                SET resolved_at = CURRENT_TIMESTAMP
                WHERE deployment_id = %s
                  AND resolved_at IS NULL;
            """,
            (deployment_id,),
        )


def save_diagnostic(
    *,
    deployment_id: int,
    status: str,
    cause: str | None = None,
    explanation: str | None = None,
    confidence: str | None = None,
    target_phase: str | None = None,
    evidence: list[str] | None = None,
    provider_connection_id: int | None = None,
    model: str | None = None,
    raw_response: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    with get_database_connection() as connection:
        row = connection.execute(
            """
                INSERT INTO deployment_diagnostics (
                    deployment_id,
                    status,
                    cause,
                    explanation,
                    confidence,
                    target_phase,
                    evidence,
                    provider_connection_id,
                    model,
                    raw_response,
                    error_message,
                    created_at
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s::JSONB, %s, %s, %s::JSONB, %s,
                    CASE WHEN %s = 'completed' THEN CURRENT_TIMESTAMP ELSE NULL END
                )
                ON CONFLICT (deployment_id)
                DO UPDATE SET
                    status = EXCLUDED.status,
                    cause = EXCLUDED.cause,
                    explanation = EXCLUDED.explanation,
                    confidence = EXCLUDED.confidence,
                    target_phase = EXCLUDED.target_phase,
                    evidence = EXCLUDED.evidence,
                    provider_connection_id = EXCLUDED.provider_connection_id,
                    model = EXCLUDED.model,
                    raw_response = EXCLUDED.raw_response,
                    error_message = EXCLUDED.error_message,
                    created_at = CASE
                        WHEN EXCLUDED.status = 'completed'
                        THEN CURRENT_TIMESTAMP
                        ELSE deployment_diagnostics.created_at
                    END
                RETURNING *;
            """,
            (
                deployment_id,
                status,
                cause,
                explanation,
                confidence,
                target_phase,
                json.dumps(evidence or []),
                provider_connection_id,
                model,
                json.dumps(raw_response) if raw_response is not None else None,
                error_message,
                status,
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("Impossible d’enregistrer le diagnostic.")
        return row


def replace_corrections(
    deployment_id: int,
    corrections: list[dict[str, Any]],
) -> None:
    with get_database_connection() as connection:
        connection.execute(
            """
                DELETE FROM deployment_corrections
                WHERE deployment_id = %s
                  AND status = 'proposed';
            """,
            (deployment_id,),
        )
        for correction in corrections:
            connection.execute(
                """
                    INSERT INTO deployment_corrections (
                        deployment_id,
                        title,
                        summary,
                        target_phase,
                        target_file,
                        diff,
                        risk,
                        status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'proposed');
                """,
                (
                    deployment_id,
                    correction["title"],
                    correction["summary"],
                    correction["target_phase"],
                    correction.get("target_file"),
                    correction.get("diff"),
                    correction.get("risk") or "medium",
                ),
            )


def add_chat_message(
    *,
    deployment_id: int,
    role: str,
    content: str,
    user_id: int | None = None,
) -> dict[str, Any]:
    with get_database_connection() as connection:
        row = connection.execute(
            """
                INSERT INTO deployment_chat_messages (
                    deployment_id,
                    role,
                    content,
                    created_by
                )
                VALUES (%s, %s, %s, %s)
                RETURNING *;
            """,
            (deployment_id, role, content, user_id),
        ).fetchone()
        if row is None:
            raise RuntimeError("Impossible d’enregistrer le message.")
        return row


def approve_correction(
    *,
    deployment_id: int,
    correction_id: int,
    user_id: int,
) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        return connection.execute(
            """
                UPDATE deployment_corrections
                SET status = 'approved',
                    approved_by = %s,
                    approved_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND deployment_id = %s
                  AND status = 'proposed'
                RETURNING *;
            """,
            (user_id, correction_id, deployment_id),
        ).fetchone()
