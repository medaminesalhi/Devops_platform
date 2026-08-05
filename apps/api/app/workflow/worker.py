from __future__ import annotations

import time

from pathlib import Path
from typing import Any

import click

from flask import (
    Flask,
    current_app,
)

from app.database import (
    get_database_connection,
)

from app.generation.repository import (
    find_generation_context,
)

from app.generation.workspace import (
    GenerationWorkspaceError,
    generation_workspace_manager,
)

from app.workflow.ai import (
    PROMPT_VERSION,
    AiProviderError,
    execute_generation_plan,
)

from app.workflow.contracts import (
    build_ai_payload,
)

from app.workflow.generation_repository import (
    add_generation_event,
    claim_next_generation,
    fail_workflow_generation,
    mark_generation_awaiting_review,
    replace_workflow_artifacts,
    update_generation_step,
)

from app.workflow.renderers import (
    ArtifactRenderingError,
    render_project_artifacts,
)

from app.workflow.repository import (
    attach_ai_run_to_generation,
    create_ai_run,
    find_ai_connection,
    find_contract,
)


SOURCE_FILE_NAMES = {
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "angular.json",
    "vite.config.js",
    "vite.config.ts",
    "next.config.js",
    "next.config.mjs",

    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "poetry.lock",
    "Pipfile",
    "Pipfile.lock",
    "manage.py",
    "wsgi.py",
    "asgi.py",

    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",

    "go.mod",
    "go.sum",

    "composer.json",

    "Gemfile",
    "Gemfile.lock",

    "Cargo.toml",
    "Cargo.lock",

    "Dockerfile",

    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",

    "nginx.conf",

    "README",
    "README.md",
    "README.txt",
}


SOURCE_FILE_SUFFIXES = {
    ".csproj",
    ".fsproj",
    ".vbproj",
}


SOURCE_EXCLUDED_PARTS = {
    ".git",
    ".idea",
    ".vscode",

    ".venv",
    "venv",

    "node_modules",

    "dist",
    "build",
    "target",
    "coverage",

    "__pycache__",
}


SENSITIVE_SOURCE_NAMES = {
    ".env",
    "id_rsa",
    "id_ed25519",
    "kubeconfig",
    "credentials",
    "secrets.yaml",
    "secret.yaml",
}


class WorkflowWorkerError(
    RuntimeError
):
    def __init__(
        self,
        code: str,
        message: str,
    ) -> None:
        super().__init__(
            message
        )

        self.code = code
        self.message = message


def register_workflow_commands(
    app: Flask,
) -> None:
    """
    Enregistre deux commandes Flask :

    workflow-worker :
        worker permanent.

    workflow-run ID :
        relance locale d'une génération précise.
    """

    @app.cli.command(
        "workflow-worker"
    )
    @click.option(
        "--poll-seconds",

        type=float,

        default=2.0,

        show_default=True,

        help=(
            "Délai entre deux recherches "
            "de tâches."
        ),
    )
    @click.option(
        "--once",

        is_flag=True,

        help=(
            "Traite au maximum une tâche "
            "puis s'arrête."
        ),
    )
    @click.option(
        "--recover-after-minutes",

        type=int,

        default=30,

        show_default=True,

        help=(
            "Remet en file les tâches "
            "abandonnées après un arrêt."
        ),
    )
    def workflow_worker_command(
        poll_seconds: float,
        once: bool,
        recover_after_minutes: int,
    ) -> None:
        recovered = (
            recover_abandoned_generations(
                max(
                    5,
                    recover_after_minutes,
                )
            )
        )

        if recovered:
            click.echo(
                (
                    f"{recovered} génération(s) "
                    "abandonnée(s) remise(s) "
                    "en file."
                )
            )

        click.echo(
            "Worker SApixi prêt."
        )

        while True:
            generation = (
                claim_next_generation()
            )

            if generation is None:
                if once:
                    click.echo(
                        (
                            "Aucune génération "
                            "en attente."
                        )
                    )

                    return

                time.sleep(
                    max(
                        0.5,
                        poll_seconds,
                    )
                )

                continue

            generation_run_id = int(
                generation["id"]
            )

            click.echo(
                (
                    "Traitement de la génération "
                    f"{generation_run_id}."
                )
            )

            run_generation_job(
                generation
            )

            if once:
                return


    @app.cli.command(
        "workflow-run"
    )
    @click.argument(
        "generation_run_id",
        type=int,
    )
    def workflow_run_command(
        generation_run_id: int,
    ) -> None:
        """
        Relance une génération précise.

        Cette commande est surtout utile
        pendant le développement local.
        """

        with get_database_connection() as database:
            row = database.execute(
                """
                    SELECT id

                    FROM project_generation_runs

                    WHERE
                        id = %s

                        AND contract_id
                            IS NOT NULL

                        AND status IN (
                            'pending',
                            'failed'
                        );
                """,
                (
                    generation_run_id,
                ),
            ).fetchone()

            if row is None:
                raise click.ClickException(
                    (
                        "La génération est "
                        "introuvable ou n'est "
                        "pas relançable."
                    )
                )

            database.execute(
                """
                    UPDATE project_generation_runs

                    SET
                        status =
                            'pending',

                        progress =
                            0,

                        current_step =
                            'queued',

                        error_code =
                            NULL,

                        error_message =
                            NULL,

                        started_at =
                            NULL,

                        finished_at =
                            NULL

                    WHERE id = %s;
                """,
                (
                    generation_run_id,
                ),
            )

        generation = (
            claim_next_generation()
        )

        if (
            generation is None

            or int(
                generation["id"]
            ) != generation_run_id
        ):
            raise click.ClickException(
                (
                    "La génération n'a pas pu "
                    "être réservée par ce worker."
                )
            )

        run_generation_job(
            generation
        )


def recover_abandoned_generations(
    after_minutes: int,
) -> int:
    """
    Remet en attente les tâches restées running
    après l'arrêt brutal du processus worker.
    """

    with get_database_connection() as database:
        rows = database.execute(
            """
                UPDATE project_generation_runs

                SET
                    status =
                        'pending',

                    progress =
                        0,

                    current_step =
                        'queued',

                    started_at =
                        NULL,

                    error_code =
                        NULL,

                    error_message =
                        NULL

                WHERE
                    status = 'running'

                    AND contract_id
                        IS NOT NULL

                    AND started_at < (
                        CURRENT_TIMESTAMP

                        - (
                            %s
                            * INTERVAL '1 minute'
                        )
                    )

                RETURNING id;
            """,
            (
                after_minutes,
            ),
        ).fetchall()

        for row in rows:
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
                        'warning',
                        'recovered',
                        'La génération a été remise en file après un arrêt du worker.',
                        '{}'::JSONB
                    );
                """,
                (
                    row["id"],
                ),
            )

    return len(rows)


def run_generation_job(
    generation: dict[str, Any],
) -> None:
    generation_run_id = int(
        generation["id"]
    )

    project_id = int(
        generation["project_id"]
    )

    try:
        contract_id = generation.get(
            "contract_id"
        )

        if contract_id is None:
            raise WorkflowWorkerError(
                "CONTRACT_REQUIRED",

                (
                    "La génération ne possède "
                    "aucun contrat de déploiement."
                ),
            )

        update_generation_step(
            generation_run_id=
                generation_run_id,

            project_id=
                project_id,

            progress=
                5,

            step=
                "loading_contract",

            message=(
                "Chargement du contrat "
                "confirmé."
            ),
        )

        contract_row = find_contract(
            int(contract_id)
        )

        if (
            contract_row is None

            or int(
                contract_row[
                    "project_id"
                ]
            ) != project_id
        ):
            raise WorkflowWorkerError(
                "CONTRACT_NOT_FOUND",

                (
                    "Le contrat associé à "
                    "la génération est "
                    "introuvable."
                ),
            )

        if (
            contract_row["status"]
            != "confirmed"
        ):
            raise WorkflowWorkerError(
                "CONTRACT_NOT_CONFIRMED",

                (
                    "Le contrat associé "
                    "n'est plus confirmé."
                ),
            )

        contract = contract_row.get(
            "contract"
        )

        if not isinstance(
            contract,
            dict,
        ):
            raise WorkflowWorkerError(
                "CONTRACT_INVALID",

                (
                    "Le contenu du contrat "
                    "est invalide."
                ),
            )

        source_context = (
            find_generation_context(
                project_id
            )
        )

        if source_context is None:
            raise WorkflowWorkerError(
                "PROJECT_NOT_FOUND",
                "Le projet est introuvable.",
            )

        expected_analysis_id = int(
            contract_row[
                "analysis_run_id"
            ]
        )

        actual_analysis_id = (
            source_context.get(
                "confirmed_analysis_run_id"
            )
        )

        if (
            actual_analysis_id is None

            or int(
                actual_analysis_id
            ) != expected_analysis_id
        ):
            raise WorkflowWorkerError(
                "ANALYSIS_VERSION_CHANGED",

                (
                    "L'analyse confirmée du projet "
                    "a changé. Créez un nouveau "
                    "contrat de déploiement."
                ),
            )

        update_generation_step(
            generation_run_id=
                generation_run_id,

            project_id=
                project_id,

            progress=
                12,

            step=
                "preparing_source",

            message=(
                "Préparation de la version "
                "exacte du code source."
            ),
        )

        with (
            generation_workspace_manager
            .prepare(
                source_context
            )
        ) as prepared_source:
            source_files = (
                collect_ai_source_files(
                    source_root=
                        prepared_source
                        .source_path,

                    contract=
                        contract,
                )
            )

            ai_plan:dict[str, Any] | None = None

            generation_mode = str(
                generation.get(
                    "generation_mode"
                )
                or "hybrid"
            )

            if (
                generation_mode
                == "hybrid"
            ):
                update_generation_step(
                    generation_run_id=
                        generation_run_id,

                    project_id=
                        project_id,

                    progress=
                        25,

                    step=
                        "ai_planning",

                    message=(
                        "Demande d'un plan "
                        "structuré au fournisseur IA."
                    ),

                    details={
                        "sourceFileCount":
                            len(
                                source_files
                            ),
                    },
                )

                ai_connection_id = (
                    generation.get(
                        "ai_connection_id"
                    )
                )

                ai_model = str(
                    generation.get(
                        "ai_model"
                    )
                    or ""
                ).strip()

                if (
                    ai_connection_id is None

                    or not ai_model
                ):
                    raise WorkflowWorkerError(
                        (
                            "AI_CONFIGURATION_"
                            "REQUIRED"
                        ),

                        (
                            "La génération hybride "
                            "nécessite une connexion "
                            "et un modèle IA."
                        ),
                    )

                ai_connection = (
                    find_ai_connection(
                        int(
                            ai_connection_id
                        )
                    )
                )

                if ai_connection is None:
                    raise WorkflowWorkerError(
                        "AI_CONNECTION_NOT_FOUND",

                        (
                            "La connexion IA a été "
                            "supprimée ou désactivée."
                        ),
                    )

                ai_payload = (
                    build_ai_payload(
                        contract=
                            contract,

                        analysis_summary=(
                            source_context.get(
                                "analysis_summary"
                            )
                            or {}
                        ),

                        source_files=
                            source_files,
                    )
                )

                source_bytes = sum(
                    len(
                        str(
                            item.get(
                                "content"
                            )
                            or ""
                        ).encode(
                            "utf-8"
                        )
                    )

                    for item
                    in source_files
                )

                ai_run = create_ai_run(
                    project_id=
                        project_id,

                    contract_id=
                        int(contract_id),

                    generation_run_id=
                        generation_run_id,

                    connection_id=
                        int(
                            ai_connection_id
                        ),

                    provider_type=
                        str(
                            ai_connection[
                                "provider_type"
                            ]
                        ),

                    model_identifier=
                        ai_model,

                    run_type=
                        "generation_plan",

                    prompt_version=
                        PROMPT_VERSION,

                    request_summary={
                        "contractRevision":
                            contract_row.get(
                                "revision"
                            ),

                        "componentCount":
                            len(
                                contract.get(
                                    "components"
                                )
                                or []
                            ),

                        "sourceFileCount":
                            len(
                                source_files
                            ),

                        "sourceBytes":
                            source_bytes,
                    },

                    created_by=
                        int(
                            generation[
                                "created_by"
                            ]
                        ),
                )

                attach_ai_run_to_generation(
                    generation_run_id=
                        generation_run_id,

                    ai_run_id=
                        int(
                            ai_run["id"]
                        ),

                    connection_id=
                        int(
                            ai_connection_id
                        ),

                    model_identifier=
                        ai_model,

                    prompt_version=
                        PROMPT_VERSION,
                )

                ai_result = (
                    execute_generation_plan(
                        ai_run_id=
                            int(
                                ai_run["id"]
                            ),

                        connection_id=
                            int(
                                ai_connection_id
                            ),

                        model_identifier=
                            ai_model,

                        payload=
                            ai_payload,

                        temperature=
                            0.1,
                    )
                )

                ai_plan = (
                    ai_result.output
                )

                add_generation_event(
                    generation_run_id=
                        generation_run_id,

                    level=
                        "success",

                    step=
                        "ai_planning",

                    message=(
                        "Le plan IA structuré "
                        "a été validé."
                    ),

                    details={
                        "providerType":
                            ai_result
                            .provider_type,

                        "model":
                            ai_result
                            .model_identifier,

                        "latencyMs":
                            ai_result
                            .latency_ms,

                        "questionCount":
                            len(
                                ai_plan.get(
                                    "questions"
                                )
                                or []
                            ),

                        "warningCount":
                            len(
                                ai_plan.get(
                                    "warnings"
                                )
                                or []
                            ),
                    },
                )

                blocking_questions = [
                    question

                    for question
                    in (
                        ai_plan.get(
                            "questions"
                        )
                        or []
                    )

                    if (
                        isinstance(
                            question,
                            dict,
                        )

                        and question.get(
                            "blocking"
                        )
                    )
                ]

                if blocking_questions:
                    raise WorkflowWorkerError(
                        "AI_BLOCKING_QUESTIONS",

                        (
                            "Le fournisseur IA a "
                            "identifié des informations "
                            "bloquantes. Complétez le "
                            "contrat avant de relancer."
                        ),
                    )

            update_generation_step(
                generation_run_id=
                    generation_run_id,

                project_id=
                    project_id,

                progress=
                    55,

                step=
                    "rendering",

                message=(
                    "Génération déterministe "
                    "des artefacts Docker, "
                    "Helm et Argo CD."
                ),
            )

            (
                artifacts,
                summary,
            ) = render_project_artifacts(
                source_root=
                    prepared_source
                    .source_path,

                contract=
                    contract,

                ai_plan=
                    ai_plan,

                source_version=
                    prepared_source
                    .version,
            )

        update_generation_step(
            generation_run_id=
                generation_run_id,

            project_id=
                project_id,

            progress=
                85,

            step=
                "saving_artifacts",

            message=(
                "Enregistrement des artefacts "
                "et de leurs validations."
            ),

            details={
                "artifactCount":
                    len(artifacts),
            },
        )

        replace_workflow_artifacts(
            generation_run_id=
                generation_run_id,

            project_id=
                project_id,

            artifacts=
                artifacts,
        )

        mark_generation_awaiting_review(
            generation_run_id=
                generation_run_id,

            project_id=
                project_id,

            summary=
                summary,
        )

    except WorkflowWorkerError as error:
        fail_workflow_generation(
            generation_run_id=
                generation_run_id,

            project_id=
                project_id,

            error_code=
                error.code,

            error_message=
                error.message,
        )

    except GenerationWorkspaceError as error:
        fail_workflow_generation(
            generation_run_id=
                generation_run_id,

            project_id=
                project_id,

            error_code=
                error.code,

            error_message=
                error.message,
        )

    except AiProviderError as error:
        fail_workflow_generation(
            generation_run_id=
                generation_run_id,

            project_id=
                project_id,

            error_code=
                error.code,

            error_message=
                str(error),
        )

    except ArtifactRenderingError as error:
        fail_workflow_generation(
            generation_run_id=
                generation_run_id,

            project_id=
                project_id,

            error_code=
                "ARTIFACT_RENDERING_FAILED",

            error_message=
                str(error),
        )

    except Exception as error:
        current_app.logger.exception(
            (
                "Échec inattendu de "
                "la génération %s."
            ),
            generation_run_id,
        )

        fail_workflow_generation(
            generation_run_id=
                generation_run_id,

            project_id=
                project_id,

            error_code=
                "WORKFLOW_UNEXPECTED_ERROR",

            error_message=(
                "Une erreur inattendue a "
                "interrompu la génération. "
                f"Détail technique : {error}"
            ),
        )


def collect_ai_source_files(
    *,
    source_root: Path,
    contract: dict[str, Any],
) -> list[dict[str, str]]:
    """
    Sélectionne un contexte limité et sans secrets.

    L'IA ne reçoit pas tout le repository.
    """

    policies = contract.get(
        "policies"
    )

    maximum_bytes = 200_000

    if isinstance(
        policies,
        dict,
    ):
        try:
            maximum_bytes = int(
                policies.get(
                    "maximumAiContextBytes",
                    maximum_bytes,
                )
            )

        except (
            TypeError,
            ValueError,
        ):
            maximum_bytes = 200_000

    maximum_bytes = min(
        500_000,

        max(
            20_000,
            maximum_bytes,
        ),
    )

    candidate_roots: list[
        Path
    ] = [
        source_root,
    ]

    components = contract.get(
        "components"
    )

    if isinstance(
        components,
        list,
    ):
        for component in components:
            if not isinstance(
                component,
                dict,
            ):
                continue

            root_path = str(
                component.get(
                    "rootPath"
                )
                or "."
            )

            candidate = (
                source_root
                / root_path
            ).resolve()

            try:
                candidate.relative_to(
                    source_root.resolve()
                )

            except ValueError:
                continue

            if (
                candidate.is_dir()

                and candidate
                not in candidate_roots
            ):
                candidate_roots.append(
                    candidate
                )

    selected_paths: set[
        Path
    ] = set()

    for root in candidate_roots:
        for path in sorted(
            root.rglob("*")
        ):
            if (
                len(selected_paths)
                >= 60
            ):
                break

            if (
                not path.is_file()

                or path.is_symlink()
            ):
                continue

            relative = path.relative_to(
                source_root
            )

            if any(
                part
                in SOURCE_EXCLUDED_PARTS

                for part
                in relative.parts
            ):
                continue

            if (
                path.name
                in SENSITIVE_SOURCE_NAMES

                or path.name.startswith(
                    ".env"
                )
            ):
                continue

            if (
                path.name
                not in SOURCE_FILE_NAMES

                and path.suffix.lower()
                not in SOURCE_FILE_SUFFIXES
            ):
                continue

            if (
                path.stat().st_size
                > 50_000
            ):
                continue

            selected_paths.add(
                path
            )

    result: list[
        dict[str, str]
    ] = []

    used_bytes = 0

    for path in sorted(
        selected_paths
    ):
        content = path.read_text(
            encoding="utf-8",
            errors="replace",
        )

        content_bytes = len(
            content.encode(
                "utf-8"
            )
        )

        if (
            used_bytes
            + content_bytes
            > maximum_bytes
        ):
            break

        used_bytes += (
            content_bytes
        )

        result.append(
            {
                "path":
                    str(
                        path.relative_to(
                            source_root
                        )
                    ).replace(
                        "\\",
                        "/",
                    ),

                "content":
                    content,
            }
        )

    return result