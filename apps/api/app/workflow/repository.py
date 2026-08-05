from __future__ import annotations

import json
from typing import Any

from app.database import get_database_connection


AI_PROVIDER_TYPES = (
    "litellm",
    "ollama",
    "openai_compatible",
    "vllm",
)


CONTRACT_SELECT = """
SELECT
    c.id,
    c.project_id,
    c.analysis_run_id,
    c.environment_id,
    c.status,
    c.revision,
    c.namespace,
    c.domain,
    c.contract,
    c.validation,
    c.created_by,
    c.updated_by,
    c.confirmed_by,
    c.created_at,
    c.updated_at,
    c.confirmed_at,

    p.name AS project_name,
    p.slug AS project_slug,

    a.analyzed_commit_sha,
    a.confirmed_at AS analysis_confirmed_at,

    e.name AS environment_name,
    e.code AS environment_code,
    e.environment_type

FROM project_deployment_contracts c

JOIN projects p
    ON p.id = c.project_id

JOIN project_analysis_runs a
    ON a.id = c.analysis_run_id

JOIN deployment_environments e
    ON e.id = c.environment_id
"""


AI_RUN_SELECT = """
SELECT
    r.id,
    r.project_id,
    r.contract_id,
    r.generation_run_id,
    r.connection_id,
    r.provider_type,
    r.model_identifier,
    r.run_type,
    r.prompt_version,
    r.status,
    r.request_summary,
    r.response_json,
    r.latency_ms,
    r.error_code,
    r.error_message,
    r.created_by,
    r.created_at,
    r.started_at,
    r.finished_at,

    i.name AS connection_name,
    i.base_url AS connection_base_url

FROM project_ai_runs r

LEFT JOIN integration_connections i
    ON i.id = r.connection_id
"""


def find_project_workflow_context(
    project_id: int,
) -> dict[str, Any] | None:
    """
    Retourne le projet, sa dernière analyse confirmée
    et le dernier contrat non remplacé.
    """

    query = """
    SELECT
        p.id,
        p.name,
        p.slug,
        p.description,
        p.operation_mode,
        p.source_type,
        p.status,
        p.default_environment_id,
        p.analysis_status,
        p.generation_status,
        p.deployment_contract_status,
        p.latest_deployment_contract_id,

        a.id AS confirmed_analysis_run_id,
        a.analyzed_commit_sha,
        a.selected_subdirectory,
        a.summary AS analysis_summary,
        a.confirmed_at AS analysis_confirmed_at,

        c.id AS latest_contract_id,
        c.status AS latest_contract_status,
        c.revision AS latest_contract_revision,
        c.environment_id AS latest_contract_environment_id,
        c.updated_at AS latest_contract_updated_at

    FROM projects p

    LEFT JOIN LATERAL (
        SELECT *

        FROM project_analysis_runs

        WHERE
            project_id = p.id
            AND status = 'confirmed'

        ORDER BY
            confirmed_at DESC NULLS LAST,
            created_at DESC

        LIMIT 1
    ) a
        ON TRUE

    LEFT JOIN LATERAL (
        SELECT *

        FROM project_deployment_contracts

        WHERE
            project_id = p.id
            AND status <> 'superseded'

        ORDER BY
            updated_at DESC,
            id DESC

        LIMIT 1
    ) c
        ON TRUE

    WHERE
        p.id = %s
        AND p.archived_at IS NULL

    LIMIT 1;
    """

    with get_database_connection() as database:
        return database.execute(
            query,
            (project_id,),
        ).fetchone()


def list_analysis_components(
    analysis_run_id: int,
) -> list[dict[str, Any]]:
    """
    Retourne tous les composants de l'analyse confirmée.

    Les composants non déployables restent présents pour que
    l'utilisateur puisse voir et confirmer leur classification.
    """

    query = """
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

    ORDER BY
        root_path,
        name;
    """

    with get_database_connection() as database:
        return database.execute(
            query,
            (analysis_run_id,),
        ).fetchall()


def _environment_select(
    where_clause: str,
) -> str:
    """
    Génère la requête commune des environnements.

    Les services liés sont renvoyés dans un tableau JSONB.
    """

    return f"""
    SELECT
        e.id,
        e.name,
        e.code,
        e.environment_type,
        e.description,
        e.namespace,
        e.domain,
        e.configuration_status,
        e.is_default,

        COALESCE(
            (
                SELECT JSONB_AGG(
                    JSONB_BUILD_OBJECT(
                        'role',
                            l.service_role,

                        'required',
                            l.is_required,

                        'connectionId',
                            i.id,

                        'connectionName',
                            i.name,

                        'providerType',
                            i.provider_type,

                        'baseUrl',
                            i.base_url,

                        'status',
                            i.status,

                        'lastCheckedAt',
                            i.last_checked_at,

                        'lastLatencyMs',
                            i.last_latency_ms
                    )

                    ORDER BY
                        l.service_role
                )

                FROM environment_connections l

                JOIN integration_connections i
                    ON i.id = l.connection_id

                WHERE
                    l.environment_id = e.id
            ),

            '[]'::JSONB
        ) AS services

    FROM deployment_environments e

    WHERE
        e.configuration_status <> 'archived'

        {where_clause}
    """


def list_contract_environments(
) -> list[dict[str, Any]]:
    query = _environment_select(
        "ORDER BY e.name;"
    )

    with get_database_connection() as database:
        return database.execute(
            query
        ).fetchall()


def find_contract_environment(
    environment_id: int,
) -> dict[str, Any] | None:
    query = _environment_select(
        """
        AND e.id = %s

        LIMIT 1;
        """
    )

    with get_database_connection() as database:
        return database.execute(
            query,
            (environment_id,),
        ).fetchone()


def find_contract(
    contract_id: int,
) -> dict[str, Any] | None:
    query = f"""
    {CONTRACT_SELECT}

    WHERE c.id = %s

    LIMIT 1;
    """

    with get_database_connection() as database:
        return database.execute(
            query,
            (contract_id,),
        ).fetchone()


def find_contract_for_project(
    *,
    project_id: int,
    contract_id: int,
) -> dict[str, Any] | None:
    query = f"""
    {CONTRACT_SELECT}

    WHERE
        c.id = %s
        AND c.project_id = %s

    LIMIT 1;
    """

    with get_database_connection() as database:
        return database.execute(
            query,
            (
                contract_id,
                project_id,
            ),
        ).fetchone()


def find_latest_contract(
    project_id: int,
) -> dict[str, Any] | None:
    query = f"""
    {CONTRACT_SELECT}

    WHERE
        c.project_id = %s
        AND c.status <> 'superseded'

    ORDER BY
        c.updated_at DESC,
        c.id DESC

    LIMIT 1;
    """

    with get_database_connection() as database:
        return database.execute(
            query,
            (project_id,),
        ).fetchone()


def save_contract(
    *,
    project_id: int,
    analysis_run_id: int,
    environment_id: int,
    namespace: str,
    domain: str | None,
    contract: dict[str, Any],
    validation: dict[str, Any],
    user_id: int,
) -> dict[str, Any]:
    """
    Crée ou met à jour le contrat.

    Une modification d'un contrat confirmé :
    - augmente la révision ;
    - repasse le contrat en brouillon ;
    - annule sa confirmation précédente.
    """

    with get_database_connection() as database:
        context = database.execute(
            """
            SELECT
                p.id

            FROM projects p

            JOIN project_analysis_runs a
                ON a.project_id = p.id

            JOIN deployment_environments e
                ON e.id = %s

            WHERE
                p.id = %s
                AND p.archived_at IS NULL

                AND a.id = %s
                AND a.status = 'confirmed'

                AND e.configuration_status
                    <> 'archived'

            LIMIT 1;
            """,
            (
                environment_id,
                project_id,
                analysis_run_id,
            ),
        ).fetchone()

        if context is None:
            raise ValueError(
                "Le projet, l'analyse confirmée ou "
                "l'environnement est invalide."
            )

        row = database.execute(
            """
            INSERT INTO project_deployment_contracts (
                project_id,
                analysis_run_id,
                environment_id,
                status,
                revision,
                namespace,
                domain,
                contract,
                validation,
                created_by,
                updated_by
            )
            VALUES (
                %s,
                %s,
                %s,
                'draft',
                1,
                %s,
                %s,
                %s::JSONB,
                %s::JSONB,
                %s,
                %s
            )

            ON CONFLICT (
                project_id,
                analysis_run_id,
                environment_id
            )

            DO UPDATE SET
                status = 'draft',

                revision =
                    project_deployment_contracts.revision
                    + 1,

                namespace =
                    EXCLUDED.namespace,

                domain =
                    EXCLUDED.domain,

                contract =
                    EXCLUDED.contract,

                validation =
                    EXCLUDED.validation,

                updated_by =
                    EXCLUDED.updated_by,

                updated_at =
                    CURRENT_TIMESTAMP,

                confirmed_by =
                    NULL,

                confirmed_at =
                    NULL

            RETURNING id;
            """,
            (
                project_id,
                analysis_run_id,
                environment_id,
                namespace,
                domain,
                json.dumps(
                    contract
                ),
                json.dumps(
                    validation
                ),
                user_id,
                user_id,
            ),
        ).fetchone()

        if row is None:
            raise RuntimeError(
                "Le contrat n'a pas pu être enregistré."
            )

        contract_id = int(
            row["id"]
        )

        database.execute(
            """
            UPDATE projects

            SET
                deployment_contract_status =
                    'draft',

                latest_deployment_contract_id =
                    %s,

                updated_at =
                    CURRENT_TIMESTAMP

            WHERE id = %s;
            """,
            (
                contract_id,
                project_id,
            ),
        )

    saved_contract = find_contract(
        contract_id
    )

    if saved_contract is None:
        raise RuntimeError(
            "Impossible de relire le contrat enregistré."
        )

    return saved_contract


def confirm_contract(
    *,
    project_id: int,
    contract_id: int,
    user_id: int,
) -> dict[str, Any]:
    """
    Confirme uniquement un contrat dont validation.valid vaut true.
    """

    with get_database_connection() as database:
        current_contract = database.execute(
            """
            SELECT
                id,
                validation

            FROM project_deployment_contracts

            WHERE
                id = %s
                AND project_id = %s

            FOR UPDATE;
            """,
            (
                contract_id,
                project_id,
            ),
        ).fetchone()

        if current_contract is None:
            raise ValueError(
                "Le contrat de déploiement est introuvable."
            )

        validation = (
            current_contract.get(
                "validation"
            )
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
                "Le contrat contient encore des erreurs "
                "et ne peut pas être confirmé."
            )

        database.execute(
            """
            UPDATE project_deployment_contracts

            SET status = 'superseded'

            WHERE
                project_id = %s
                AND id <> %s
                AND status = 'confirmed';
            """,
            (
                project_id,
                contract_id,
            ),
        )

        database.execute(
            """
            UPDATE project_deployment_contracts

            SET
                status =
                    'confirmed',

                confirmed_by =
                    %s,

                confirmed_at =
                    CURRENT_TIMESTAMP,

                updated_by =
                    %s,

                updated_at =
                    CURRENT_TIMESTAMP

            WHERE id = %s;
            """,
            (
                user_id,
                user_id,
                contract_id,
            ),
        )

        database.execute(
            """
            UPDATE projects

            SET
                deployment_contract_status =
                    'confirmed',

                latest_deployment_contract_id =
                    %s,

                updated_at =
                    CURRENT_TIMESTAMP

            WHERE id = %s;
            """,
            (
                contract_id,
                project_id,
            ),
        )

    confirmed_contract = find_contract(
        contract_id
    )

    if confirmed_contract is None:
        raise RuntimeError(
            "Impossible de relire le contrat confirmé."
        )

    return confirmed_contract


def list_ai_connections(
) -> list[dict[str, Any]]:
    """
    Liste publique des connexions IA.

    secret_ciphertext n'est pas sélectionné ici.
    """

    query = """
    SELECT
        i.id,
        i.name,
        i.provider_type,
        i.base_url,
        i.description,
        i.enabled,
        i.verify_ssl,
        i.status,
        i.last_checked_at,
        i.last_latency_ms,

        COALESCE(
            c.auth_type,
            'none'
        ) AS auth_type,

        c.username,

        (
            c.secret_ciphertext
            IS NOT NULL
        ) AS credential_configured

    FROM integration_connections i

    LEFT JOIN integration_credentials c
        ON c.connection_id = i.id

    WHERE
        i.enabled = TRUE

        AND i.provider_type::TEXT =
            ANY(%s::TEXT[])

    ORDER BY
        CASE i.status
            WHEN 'online'
                THEN 0

            WHEN 'degraded'
                THEN 1

            WHEN 'unchecked'
                THEN 2

            WHEN 'not_configured'
                THEN 3

            ELSE 4
        END,

        i.name;
    """

    with get_database_connection() as database:
        return database.execute(
            query,
            (
                list(
                    AI_PROVIDER_TYPES
                ),
            ),
        ).fetchall()


def find_ai_connection(
    connection_id: int,
) -> dict[str, Any] | None:
    """
    Retourne une connexion IA complète pour un usage backend.

    Cette fonction contient secret_ciphertext et ne doit jamais
    être sérialisée directement vers Angular.
    """

    query = """
    SELECT
        i.id,
        i.name,
        i.provider_type,
        i.base_url,
        i.description,
        i.enabled,
        i.verify_ssl,
        i.status,
        i.last_checked_at,
        i.last_latency_ms,

        COALESCE(
            c.auth_type,
            'none'
        ) AS auth_type,

        c.username,
        c.secret_ciphertext,

        (
            c.secret_ciphertext
            IS NOT NULL
        ) AS credential_configured

    FROM integration_connections i

    LEFT JOIN integration_credentials c
        ON c.connection_id = i.id

    WHERE
        i.id = %s
        AND i.enabled = TRUE

        AND i.provider_type::TEXT =
            ANY(%s::TEXT[])

    LIMIT 1;
    """

    with get_database_connection() as database:
        return database.execute(
            query,
            (
                connection_id,
                list(
                    AI_PROVIDER_TYPES
                ),
            ),
        ).fetchone()


def create_ai_run(
    *,
    project_id: int,
    contract_id: int | None,
    generation_run_id: int | None,
    connection_id: int | None,
    provider_type: str | None,
    model_identifier: str | None,
    run_type: str,
    prompt_version: str,
    request_summary: dict[str, Any],
    created_by: int,
) -> dict[str, Any]:
    with get_database_connection() as database:
        row = database.execute(
            """
            INSERT INTO project_ai_runs (
                project_id,
                contract_id,
                generation_run_id,
                connection_id,
                provider_type,
                model_identifier,
                run_type,
                prompt_version,
                status,
                request_summary,
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
                'pending',
                %s::JSONB,
                %s
            )
            RETURNING id;
            """,
            (
                project_id,
                contract_id,
                generation_run_id,
                connection_id,
                provider_type,
                model_identifier,
                run_type,
                prompt_version,
                json.dumps(
                    request_summary
                ),
                created_by,
            ),
        ).fetchone()

        if row is None:
            raise RuntimeError(
                "Impossible de créer l'exécution IA."
            )

        ai_run_id = int(
            row["id"]
        )

    created_run = find_ai_run(
        ai_run_id
    )

    if created_run is None:
        raise RuntimeError(
            "Impossible de relire l'exécution IA."
        )

    return created_run


def mark_ai_run_running(
    ai_run_id: int,
) -> None:
    with get_database_connection() as database:
        database.execute(
            """
            UPDATE project_ai_runs

            SET
                status =
                    'running',

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
                ai_run_id,
            ),
        )


def complete_ai_run(
    *,
    ai_run_id: int,
    response_json: dict[str, Any],
    latency_ms: int,
) -> None:
    with get_database_connection() as database:
        database.execute(
            """
            UPDATE project_ai_runs

            SET
                status =
                    'completed',

                response_json =
                    %s::JSONB,

                latency_ms =
                    %s,

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
                    response_json
                ),
                max(
                    0,
                    int(latency_ms),
                ),
                ai_run_id,
            ),
        )


def fail_ai_run(
    *,
    ai_run_id: int,
    error_code: str,
    error_message: str,
    latency_ms: int | None = None,
) -> None:
    with get_database_connection() as database:
        database.execute(
            """
            UPDATE project_ai_runs

            SET
                status =
                    'failed',

                latency_ms =
                    %s,

                error_code =
                    %s,

                error_message =
                    %s,

                finished_at =
                    CURRENT_TIMESTAMP

            WHERE id = %s;
            """,
            (
                latency_ms,
                error_code[:100],
                error_message[:8000],
                ai_run_id,
            ),
        )


def find_ai_run(
    ai_run_id: int,
) -> dict[str, Any] | None:
    query = f"""
    {AI_RUN_SELECT}

    WHERE r.id = %s

    LIMIT 1;
    """

    with get_database_connection() as database:
        return database.execute(
            query,
            (
                ai_run_id,
            ),
        ).fetchone()


def find_ai_run_for_project(
    *,
    project_id: int,
    ai_run_id: int,
) -> dict[str, Any] | None:
    query = f"""
    {AI_RUN_SELECT}

    WHERE
        r.id = %s
        AND r.project_id = %s

    LIMIT 1;
    """

    with get_database_connection() as database:
        return database.execute(
            query,
            (
                ai_run_id,
                project_id,
            ),
        ).fetchone()


def list_ai_runs(
    *,
    project_id: int,
    limit: int = 30,
) -> list[dict[str, Any]]:
    safe_limit = min(
        100,
        max(
            1,
            int(limit),
        ),
    )

    query = f"""
    {AI_RUN_SELECT}

    WHERE r.project_id = %s

    ORDER BY
        r.created_at DESC

    LIMIT %s;
    """

    with get_database_connection() as database:
        return database.execute(
            query,
            (
                project_id,
                safe_limit,
            ),
        ).fetchall()


def attach_ai_run_to_generation(
    *,
    generation_run_id: int,
    ai_run_id: int,
    connection_id: int,
    model_identifier: str,
    prompt_version: str,
) -> None:
    """
    Relie les deux historiques après création du generation_run.
    """

    with get_database_connection() as database:
        database.execute(
            """
            UPDATE project_generation_runs

            SET
                ai_run_id =
                    %s,

                ai_connection_id =
                    %s,

                ai_model =
                    %s,

                prompt_version =
                    %s

            WHERE id = %s;
            """,
            (
                ai_run_id,
                connection_id,
                model_identifier,
                prompt_version,
                generation_run_id,
            ),
        )

        database.execute(
            """
            UPDATE project_ai_runs

            SET
                generation_run_id =
                    %s

            WHERE id = %s;
            """,
            (
                generation_run_id,
                ai_run_id,
            ),
        )