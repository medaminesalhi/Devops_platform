from __future__ import annotations

import hashlib
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
        generation.contract_id,
        generation.ai_run_id,
        generation.ai_connection_id,
        generation.ai_model,
        generation.generation_mode,
        generation.prompt_version,
        generation.status,
        generation.progress,
        generation.current_step,
        generation.summary,
        generation.error_code,
        generation.error_message,
        generation.created_by,
        generation.confirmed_by,
        generation.created_at,
        generation.started_at,
        generation.finished_at,
        generation.confirmed_at,

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
            AS environment_domain,

        contract.status
            AS contract_status,

        contract.revision
            AS contract_revision,

        contract.contract
            AS deployment_contract,

        contract.validation
            AS contract_validation

    FROM project_generation_runs
        AS generation

    INNER JOIN projects
        AS project

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

    LEFT JOIN project_deployment_contracts
        AS contract

        ON contract.id =
            generation.contract_id
"""


ARTIFACT_SELECT = """
    SELECT
        artifact.id,
        artifact.generation_run_id,
        artifact.project_id,
        artifact.component_id,
        artifact.artifact_type,
        artifact.relative_path,
        artifact.content,
        artifact.original_content,
        artifact.content_sha256,
        artifact.artifact_status,
        artifact.review_status,
        artifact.validation_status,
        artifact.validation_messages,
        artifact.review_comment,
        artifact.reviewed_by,
        artifact.reviewed_at,
        artifact.edited_by,
        artifact.edited_at,
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
"""


def create_workflow_generation(
    *,
    project_id: int,
    contract_id: int,
    generation_mode: str,
    ai_connection_id: int | None,
    ai_model: str | None,
    created_by: int,
) -> dict[str, Any]:
    """
    Crée une tâche durable dans PostgreSQL.

    La route HTTP ne réalise pas directement
    la génération. Elle crée une ligne pending
    que le worker prendra ensuite en charge.
    """

    if generation_mode not in {
        "hybrid",
        "deterministic",
    }:
        raise ValueError(
            "Le mode de génération est invalide."
        )

    with get_database_connection() as database:
        contract = database.execute(
            """
                SELECT
                    contract.id,
                    contract.project_id,
                    contract.analysis_run_id,
                    contract.environment_id,
                    contract.status,
                    contract.validation

                FROM project_deployment_contracts
                    AS contract

                WHERE
                    contract.id = %s

                    AND contract.project_id = %s

                FOR UPDATE;
            """,
            (
                contract_id,
                project_id,
            ),
        ).fetchone()

        if contract is None:
            raise ValueError(
                (
                    "Le contrat de déploiement "
                    "est introuvable."
                )
            )

        if contract["status"] != "confirmed":
            raise ValueError(
                (
                    "Le contrat doit être confirmé "
                    "avant la génération."
                )
            )

        validation = (
            contract.get("validation")
            or {}
        )

        if (
            not isinstance(
                validation,
                dict,
            )

            or not validation.get(
                "valid"
            )
        ):
            raise ValueError(
                (
                    "Le contrat confirmé "
                    "n'est pas valide."
                )
            )

        active_generation = database.execute(
            """
                SELECT id

                FROM project_generation_runs

                WHERE
                    project_id = %s

                    AND status IN (
                        'pending',
                        'running'
                    )

                LIMIT 1;
            """,
            (
                project_id,
            ),
        ).fetchone()

        if active_generation is not None:
            raise ValueError(
                (
                    "Une génération est déjà "
                    "en cours pour ce projet."
                )
            )

        row = database.execute(
            """
                INSERT INTO
                    project_generation_runs (
                        project_id,
                        analysis_run_id,
                        environment_id,
                        contract_id,
                        ai_connection_id,
                        ai_model,
                        generation_mode,
                        prompt_version,
                        status,
                        progress,
                        current_step,
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
                    NULL,
                    'pending',
                    0,
                    'queued',
                    %s
                )

                RETURNING id;
            """,
            (
                project_id,
                contract[
                    "analysis_run_id"
                ],
                contract[
                    "environment_id"
                ],
                contract_id,
                ai_connection_id,
                ai_model,
                generation_mode,
                created_by,
            ),
        ).fetchone()

        if row is None:
            raise RuntimeError(
                (
                    "Impossible de créer "
                    "la génération."
                )
            )

        generation_run_id = int(
            row["id"]
        )

        database.execute(
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
                generation_run_id,
                project_id,
            ),
        )

        database.execute(
            """
                INSERT INTO
                    project_generation_events (
                        generation_run_id,
                        level,
                        step,
                        message,
                        details
                    )

                VALUES (
                    %s,
                    'info',
                    'queued',
                    'Génération ajoutée à la file du worker.',
                    %s::JSONB
                );
            """,
            (
                generation_run_id,

                json.dumps(
                    {
                        "contractId":
                            contract_id,

                        "generationMode":
                            generation_mode,
                    }
                ),
            ),
        )

    generation = find_generation(
        generation_run_id
    )

    if generation is None:
        raise RuntimeError(
            (
                "Impossible de relire "
                "la génération créée."
            )
        )

    return generation


def claim_next_generation(
) -> dict[str, Any] | None:
    """
    Réserve atomiquement la prochaine tâche.

    SKIP LOCKED permet d'exécuter plusieurs workers
    sans que deux workers prennent la même tâche.
    """

    with get_database_connection() as database:
        row = database.execute(
            """
                SELECT
                    id,
                    project_id

                FROM project_generation_runs

                WHERE
                    status = 'pending'

                    AND contract_id
                        IS NOT NULL

                ORDER BY
                    created_at,
                    id

                FOR UPDATE SKIP LOCKED

                LIMIT 1;
            """
        ).fetchone()

        if row is None:
            return None

        generation_run_id = int(
            row["id"]
        )

        project_id = int(
            row["project_id"]
        )

        database.execute(
            """
                UPDATE project_generation_runs

                SET
                    status =
                        'running',

                    progress =
                        1,

                    current_step =
                        'loading_context',

                    started_at =
                        COALESCE(
                            started_at,
                            CURRENT_TIMESTAMP
                        ),

                    error_code =
                        NULL,

                    error_message =
                        NULL

                WHERE id = %s;
            """,
            (
                generation_run_id,
            ),
        )

        database.execute(
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

    return find_generation(
        generation_run_id
    )


def find_generation(
    generation_run_id: int,
) -> dict[str, Any] | None:
    query = f"""
        {GENERATION_SELECT}

        WHERE generation.id = %s

        LIMIT 1;
    """

    with get_database_connection() as database:
        return database.execute(
            query,
            (
                generation_run_id,
            ),
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

    with get_database_connection() as database:
        return database.execute(
            query,
            (
                generation_run_id,
                project_id,
            ),
        ).fetchone()


def find_latest_workflow_generation(
    project_id: int,
) -> dict[str, Any] | None:
    query = f"""
        {GENERATION_SELECT}

        WHERE
            generation.project_id = %s

            AND generation.contract_id
                IS NOT NULL

        ORDER BY
            generation.created_at DESC,
            generation.id DESC

        LIMIT 1;
    """

    with get_database_connection() as database:
        return database.execute(
            query,
            (
                project_id,
            ),
        ).fetchone()


def update_generation_step(
    *,
    generation_run_id: int,
    project_id: int,
    progress: int,
    step: str,
    message: str,
    level: str = "info",
    details: dict[str, Any] | None = None,
) -> None:
    safe_progress = min(
        99,
        max(
            1,
            int(progress),
        ),
    )

    if level not in {
        "info",
        "success",
        "warning",
        "error",
    }:
        level = "info"

    with get_database_connection() as database:
        database.execute(
            """
                UPDATE project_generation_runs

                SET
                    status =
                        'running',

                    progress =
                        %s,

                    current_step =
                        %s,

                    started_at =
                        COALESCE(
                            started_at,
                            CURRENT_TIMESTAMP
                        )

                WHERE id = %s;
            """,
            (
                safe_progress,
                step,
                generation_run_id,
            ),
        )

        database.execute(
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

        database.execute(
            """
                INSERT INTO
                    project_generation_events (
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
            """,
            (
                generation_run_id,
                level,
                step,
                message,
                json.dumps(
                    details or {}
                ),
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
    if level not in {
        "info",
        "success",
        "warning",
        "error",
    }:
        level = "info"

    with get_database_connection() as database:
        database.execute(
            """
                INSERT INTO
                    project_generation_events (
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
            """,
            (
                generation_run_id,
                level,
                step,
                message,

                json.dumps(
                    details or {}
                ),
            ),
        )


def replace_workflow_artifacts(
    *,
    generation_run_id: int,
    project_id: int,
    artifacts: list[dict[str, Any]],
) -> None:
    """
    Remplace les artefacts de la génération.

    Les validations sont enregistrées avec chaque
    fichier pour être affichées dans la phase 4.
    """

    with get_database_connection() as database:
        database.execute(
            """
                DELETE FROM
                    project_generated_artifacts

                WHERE generation_run_id = %s;
            """,
            (
                generation_run_id,
            ),
        )

        for artifact in artifacts:
            content = str(
                artifact["content"]
            )

            content_sha256 = (
                artifact.get(
                    "content_sha256"
                )

                or hashlib.sha256(
                    content.encode(
                        "utf-8"
                    )
                ).hexdigest()
            )

            database.execute(
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
                            validation_status,
                            validation_messages,
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
                        %s,
                        %s::JSONB,
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

                    content,

                    artifact.get(
                        "original_content"
                    ),

                    content_sha256,

                    artifact.get(
                        "artifact_status",
                        "generated",
                    ),

                    artifact.get(
                        "validation_status",
                        "pending",
                    ),

                    json.dumps(
                        artifact.get(
                            "validation_messages"
                        )
                        or []
                    ),

                    json.dumps(
                        artifact.get(
                            "metadata"
                        )
                        or {}
                    ),
                ),
            )


def mark_generation_awaiting_review(
    *,
    generation_run_id: int,
    project_id: int,
    summary: dict[str, Any],
) -> None:
    with get_database_connection() as database:
        database.execute(
            """
                UPDATE project_generation_runs

                SET
                    status =
                        'awaiting_review',

                    progress =
                        100,

                    current_step =
                        'awaiting_review',

                    summary =
                        %s::JSONB,

                    error_code =
                        NULL,

                    error_message =
                        NULL,

                    finished_at =
                        CURRENT_TIMESTAMP

                WHERE id = %s;
            """,
            (
                json.dumps(
                    summary
                ),

                generation_run_id,
            ),
        )

        database.execute(
            """
                UPDATE projects

                SET
                    generation_status =
                        'awaiting_review',

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

        database.execute(
            """
                INSERT INTO
                    project_generation_events (
                        generation_run_id,
                        level,
                        step,
                        message,
                        details
                    )

                VALUES (
                    %s,
                    'success',
                    'awaiting_review',
                    'Les artefacts sont prêts pour la revue humaine.',
                    %s::JSONB
                );
            """,
            (
                generation_run_id,

                json.dumps(
                    summary
                ),
            ),
        )


def fail_workflow_generation(
    *,
    generation_run_id: int,
    project_id: int,
    error_code: str,
    error_message: str,
) -> None:
    with get_database_connection() as database:
        database.execute(
            """
                UPDATE project_generation_runs

                SET
                    status =
                        'failed',

                    current_step =
                        'failed',

                    error_code =
                        %s,

                    error_message =
                        %s,

                    finished_at =
                        CURRENT_TIMESTAMP

                WHERE id = %s;
            """,
            (
                error_code[:100],
                error_message[:8000],
                generation_run_id,
            ),
        )

        database.execute(
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

        database.execute(
            """
                INSERT INTO
                    project_generation_events (
                        generation_run_id,
                        level,
                        step,
                        message,
                        details
                    )

                VALUES (
                    %s,
                    'error',
                    'failed',
                    %s,
                    %s::JSONB
                );
            """,
            (
                generation_run_id,
                error_message[:8000],

                json.dumps(
                    {
                        "code":
                            error_code[:100],
                    }
                ),
            ),
        )


def list_generation_events(
    *,
    generation_run_id: int,
    after_id: int = 0,
) -> list[dict[str, Any]]:
    with get_database_connection() as database:
        return database.execute(
            """
                SELECT
                    id,
                    generation_run_id,
                    level,
                    step,
                    message,
                    details,
                    created_at

                FROM project_generation_events

                WHERE
                    generation_run_id = %s

                    AND id > %s

                ORDER BY id;
            """,
            (
                generation_run_id,

                max(
                    0,
                    int(after_id),
                ),
            ),
        ).fetchall()


def list_generation_artifacts(
    generation_run_id: int,
) -> list[dict[str, Any]]:
    query = f"""
        {ARTIFACT_SELECT}

        WHERE artifact.generation_run_id = %s

        ORDER BY
            artifact.relative_path,
            artifact.id;
    """

    with get_database_connection() as database:
        return database.execute(
            query,
            (
                generation_run_id,
            ),
        ).fetchall()


def find_generation_artifact(
    *,
    generation_run_id: int,
    artifact_id: int,
) -> dict[str, Any] | None:
    query = f"""
        {ARTIFACT_SELECT}

        WHERE
            artifact.generation_run_id = %s

            AND artifact.id = %s

        LIMIT 1;
    """

    with get_database_connection() as database:
        return database.execute(
            query,
            (
                generation_run_id,
                artifact_id,
            ),
        ).fetchone()


def update_artifact_content(
    *,
    artifact_id: int,
    generation_run_id: int,
    content: str,
    validation_status: str,
    validation_messages: list[
        dict[str, Any]
    ],
    user_id: int,
) -> dict[str, Any]:
    """
    Enregistre une modification humaine.

    Une modification annule automatiquement
    l'ancienne approbation.
    """

    content_sha256 = hashlib.sha256(
        content.encode(
            "utf-8"
        )
    ).hexdigest()

    with get_database_connection() as database:
        row = database.execute(
            """
                UPDATE project_generated_artifacts

                SET
                    content =
                        %s,

                    content_sha256 =
                        %s,

                    artifact_status =
                        'proposed_update',

                    review_status =
                        'pending_review',

                    validation_status =
                        %s,

                    validation_messages =
                        %s::JSONB,

                    review_comment =
                        NULL,

                    reviewed_by =
                        NULL,

                    reviewed_at =
                        NULL,

                    edited_by =
                        %s,

                    edited_at =
                        CURRENT_TIMESTAMP,

                    updated_at =
                        CURRENT_TIMESTAMP

                WHERE
                    id = %s

                    AND generation_run_id = %s

                RETURNING id;
            """,
            (
                content,
                content_sha256,
                validation_status,

                json.dumps(
                    validation_messages
                ),

                user_id,
                artifact_id,
                generation_run_id,
            ),
        ).fetchone()

        if row is None:
            raise ValueError(
                "L'artefact est introuvable."
            )

    artifact = find_generation_artifact(
        generation_run_id=
            generation_run_id,

        artifact_id=
            artifact_id,
    )

    if artifact is None:
        raise RuntimeError(
            (
                "Impossible de relire "
                "l'artefact modifié."
            )
        )

    return artifact


def review_artifact(
    *,
    artifact_id: int,
    generation_run_id: int,
    decision: str,
    comment: str | None,
    user_id: int,
) -> dict[str, Any]:
    """
    Approuve ou rejette un fichier.

    Un fichier dont la validation est failed
    ne peut pas être approuvé.
    """

    if decision not in {
        "approved",
        "rejected",
    }:
        raise ValueError(
            (
                "La décision de revue "
                "est invalide."
            )
        )

    with get_database_connection() as database:
        artifact = database.execute(
            """
                SELECT validation_status

                FROM project_generated_artifacts

                WHERE
                    id = %s

                    AND generation_run_id = %s

                FOR UPDATE;
            """,
            (
                artifact_id,
                generation_run_id,
            ),
        ).fetchone()

        if artifact is None:
            raise ValueError(
                "L'artefact est introuvable."
            )

        if (
            decision == "approved"

            and artifact[
                "validation_status"
            ] == "failed"
        ):
            raise ValueError(
                (
                    "Un artefact invalide "
                    "ne peut pas être approuvé."
                )
            )

        database.execute(
            """
                UPDATE project_generated_artifacts

                SET
                    review_status =
                        %s,

                    review_comment =
                        %s,

                    reviewed_by =
                        %s,

                    reviewed_at =
                        CURRENT_TIMESTAMP,

                    updated_at =
                        CURRENT_TIMESTAMP

                WHERE
                    id = %s

                    AND generation_run_id = %s;
            """,
            (
                decision,
                comment,
                user_id,
                artifact_id,
                generation_run_id,
            ),
        )

    reviewed = find_generation_artifact(
        generation_run_id=
            generation_run_id,

        artifact_id=
            artifact_id,
    )

    if reviewed is None:
        raise RuntimeError(
            (
                "Impossible de relire "
                "l'artefact revu."
            )
        )

    return reviewed


def confirm_generation_review(
    *,
    project_id: int,
    generation_run_id: int,
    user_id: int,
) -> dict[str, Any]:
    """
    Termine la phase 4.

    Conditions :
    - aucun fichier invalide ;
    - aucun fichier rejeté ;
    - tous les fichiers approuvés.
    """

    with get_database_connection() as database:
        generation = database.execute(
            """
                SELECT
                    id,
                    status

                FROM project_generation_runs

                WHERE
                    id = %s

                    AND project_id = %s

                FOR UPDATE;
            """,
            (
                generation_run_id,
                project_id,
            ),
        ).fetchone()

        if generation is None:
            raise ValueError(
                "La génération est introuvable."
            )

        if generation["status"] not in {
            "awaiting_review",
            "completed",
        }:
            raise ValueError(
                (
                    "Cette génération n'est pas "
                    "prête pour confirmation."
                )
            )

        counts = database.execute(
            """
                SELECT
                    COUNT(*)::INTEGER
                        AS total,

                    COUNT(*) FILTER (
                        WHERE
                            review_status =
                                'approved'
                    )::INTEGER
                        AS approved,

                    COUNT(*) FILTER (
                        WHERE
                            review_status =
                                'rejected'
                    )::INTEGER
                        AS rejected,

                    COUNT(*) FILTER (
                        WHERE
                            validation_status =
                                'failed'
                    )::INTEGER
                        AS invalid

                FROM project_generated_artifacts

                WHERE generation_run_id = %s;
            """,
            (
                generation_run_id,
            ),
        ).fetchone()

        if (
            counts is None

            or counts["total"] == 0
        ):
            raise ValueError(
                (
                    "La génération ne contient "
                    "aucun artefact."
                )
            )

        if counts["invalid"] > 0:
            raise ValueError(
                (
                    "Corrigez les artefacts "
                    "invalides avant confirmation."
                )
            )

        if counts["rejected"] > 0:
            raise ValueError(
                (
                    "Un ou plusieurs artefacts "
                    "sont rejetés."
                )
            )

        if (
            counts["approved"]
            != counts["total"]
        ):
            raise ValueError(
                (
                    "Tous les artefacts doivent "
                    "être approuvés."
                )
            )

        database.execute(
            """
                UPDATE project_generation_runs

                SET
                    status =
                        'confirmed',

                    current_step =
                        'confirmed',

                    confirmed_by =
                        %s,

                    confirmed_at =
                        CURRENT_TIMESTAMP

                WHERE id = %s;
            """,
            (
                user_id,
                generation_run_id,
            ),
        )

        database.execute(
            """
                UPDATE projects

                SET
                    generation_status =
                        'confirmed',

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

        database.execute(
            """
                INSERT INTO
                    project_generation_events (
                        generation_run_id,
                        level,
                        step,
                        message,
                        details
                    )

                VALUES (
                    %s,
                    'success',
                    'confirmed',
                    'La génération a été confirmée par l’utilisateur.',
                    '{}'::JSONB
                );
            """,
            (
                generation_run_id,
            ),
        )

    confirmed = find_generation(
        generation_run_id
    )

    if confirmed is None:
        raise RuntimeError(
            (
                "Impossible de relire "
                "la génération confirmée."
            )
        )

    return confirmed