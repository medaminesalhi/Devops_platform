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
    ARTIFACT_REVISION_PROMPT_VERSION,
    AiProviderError,
    execute_artifact_revision,
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
    ai_editable_artifacts,
    apply_ai_artifact_revision,
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
    ".py",
    ".pyi",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".vue",
    ".java",
    ".kt",
    ".kts",
    ".go",
    ".php",
    ".rb",
    ".rs",
    ".cs",
    ".csproj",
    ".fs",
    ".fsproj",
    ".vb",
    ".vbproj",
    ".sh",
    ".yaml",
    ".yml",
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


SOURCE_PRIORITY_TERMS = (
    "main",
    "app",
    "wsgi",
    "asgi",
    "route",
    "url",
    "view",
    "controller",
    "health",
    "config",
    "setting",
    "worker",
    "task",
    "migration",
    "entrypoint",
)


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

            ai_connection_id = generation.get("ai_connection_id")
            ai_model = str(generation.get("ai_model") or "").strip()
            ai_connection: dict[str, Any] | None = None

            if generation_mode == "hybrid":
                update_generation_step(
                    generation_run_id=generation_run_id,
                    project_id=project_id,
                    progress=25,
                    step="ai_planning",
                    message="Demande d'un plan structuré au fournisseur IA.",
                    details={"sourceFileCount": len(source_files)},
                )

                if ai_connection_id is None or not ai_model:
                    add_generation_event(
                        generation_run_id=generation_run_id,
                        level="warning",
                        step="ai_planning",
                        message=(
                            "Configuration IA incomplète. La génération continue "
                            "avec le moteur déterministe SApixi."
                        ),
                        details={
                            "fallback": True,
                            "reason": "AI_CONFIGURATION_REQUIRED",
                        },
                    )
                else:
                    ai_connection = find_ai_connection(int(ai_connection_id))

                    if ai_connection is None:
                        add_generation_event(
                            generation_run_id=generation_run_id,
                            level="warning",
                            step="ai_planning",
                            message=(
                                "La connexion IA sélectionnée n'est plus disponible. "
                                "La génération continue en mode déterministe."
                            ),
                            details={
                                "fallback": True,
                                "reason": "AI_CONNECTION_NOT_FOUND",
                                "connectionId": int(ai_connection_id),
                                "model": ai_model,
                            },
                        )
                    else:
                        ai_payload = build_ai_payload(
                            contract=contract,
                            analysis_summary=(
                                source_context.get("analysis_summary") or {}
                            ),
                            source_files=source_files,
                        )

                        source_bytes = sum(
                            len(
                                str(item.get("content") or "").encode("utf-8")
                            )
                            for item in source_files
                        )

                        ai_run = create_ai_run(
                            project_id=project_id,
                            contract_id=int(contract_id),
                            generation_run_id=generation_run_id,
                            connection_id=int(ai_connection_id),
                            provider_type=str(ai_connection["provider_type"]),
                            model_identifier=ai_model,
                            run_type="generation_plan",
                            prompt_version=PROMPT_VERSION,
                            request_summary={
                                "contractRevision": contract_row.get("revision"),
                                "componentCount": len(
                                    contract.get("components") or []
                                ),
                                "sourceFileCount": len(source_files),
                                "sourceBytes": source_bytes,
                            },
                            created_by=int(generation["created_by"]),
                        )

                        attach_ai_run_to_generation(
                            generation_run_id=generation_run_id,
                            ai_run_id=int(ai_run["id"]),
                            connection_id=int(ai_connection_id),
                            model_identifier=ai_model,
                            prompt_version=PROMPT_VERSION,
                        )

                        try:
                            ai_result = execute_generation_plan(
                                ai_run_id=int(ai_run["id"]),
                                connection_id=int(ai_connection_id),
                                model_identifier=ai_model,
                                payload=ai_payload,
                                temperature=0.1,
                            )
                        except AiProviderError as error:
                            # Le mode hybride signifie que l'IA enrichit le
                            # plan, mais ne doit pas empêcher le moteur sûr
                            # et déterministe de produire les artefacts.
                            ai_plan = None
                            add_generation_event(
                                generation_run_id=generation_run_id,
                                level="warning",
                                step="ai_planning",
                                message=(
                                    "Le fournisseur IA n'a pas fourni de plan "
                                    "exploitable. SApixi continue avec son moteur "
                                    f"déterministe. Cause : {str(error)}"
                                ),
                                details={
                                    "fallback": True,
                                    "providerType": str(
                                        ai_connection["provider_type"]
                                    ),
                                    "model": ai_model,
                                    "errorCode": error.code,
                                    "errorMessage": str(error),
                                },
                            )
                        else:
                            ai_plan = ai_result.output

                            add_generation_event(
                                generation_run_id=generation_run_id,
                                level="success",
                                step="ai_planning",
                                message="Le plan IA structuré a été validé.",
                                details={
                                    "providerType": ai_result.provider_type,
                                    "model": ai_result.model_identifier,
                                    "latencyMs": ai_result.latency_ms,
                                    "questionCount": len(
                                        ai_plan.get("questions") or []
                                    ),
                                    "warningCount": len(
                                        ai_plan.get("warnings") or []
                                    ),
                                },
                            )

                            blocking_questions = [
                                question
                                for question in (ai_plan.get("questions") or [])
                                if (
                                    isinstance(question, dict)
                                    and question.get("blocking")
                                )
                            ]

                            if blocking_questions:
                                # Le contrat est déjà confirmé à cette étape.
                                # Une question inventée par le LLM ne doit pas
                                # bloquer une génération qui reste possible à
                                # partir du contrat validé.
                                add_generation_event(
                                    generation_run_id=generation_run_id,
                                    level="warning",
                                    step="ai_planning",
                                    message=(
                                        "Le plan IA contient des questions "
                                        "bloquantes. Il est ignoré et la génération "
                                        "continue à partir du contrat confirmé."
                                    ),
                                    details={
                                        "fallback": True,
                                        "reason": "AI_BLOCKING_QUESTIONS",
                                        "questionCount": len(blocking_questions),
                                    },
                                )
                                ai_plan = None

            update_generation_step(
                generation_run_id=
                    generation_run_id,

                project_id=
                    project_id,

                progress=
                    50,

                step=
                    "rendering",

                message=(
                    "Création de la base sûre des artefacts "
                    "Docker, Helm et Argo CD."
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

            # ------------------------------------------------------------
            # PASSAGE IA 2 : Qwen reçoit les vrais artefacts candidats et
            # peut retourner leur contenu complet. Pour la V1, seuls les
            # fichiers explicitement marqués aiEditable par le renderer
            # (Dockerfile généré et deployment.yaml) sont autorisés.
            # ------------------------------------------------------------
            if (
                generation_mode == "hybrid"
                and ai_plan is not None
                and ai_connection is not None
                and ai_connection_id is not None
                and ai_model
            ):
                editable = ai_editable_artifacts(artifacts)

                if editable:
                    update_generation_step(
                        generation_run_id=generation_run_id,
                        project_id=project_id,
                        progress=68,
                        step="ai_artifact_revision",
                        message=(
                            "Qwen révise les artefacts candidats autorisés "
                            "et peut retourner leur contenu complet."
                        ),
                        details={
                            "artifactCount": len(editable),
                            "paths": [
                                item["relativePath"]
                                for item in editable
                            ],
                        },
                    )

                    revision_payload = {
                        "task": "artifact_revision",
                        "contract": contract,
                        "analysisSummary": (
                            source_context.get("analysis_summary") or {}
                        ),
                        "generationPlan": ai_plan,
                        "sourceFiles": source_files,
                        "allowedArtifacts": editable,
                        "constraints": {
                            "noSecretValues": True,
                            "noDirectExecution": True,
                            "onlyAllowedPaths": True,
                            "preserveLockedContractDecisions": True,
                        },
                    }

                    revision_run = create_ai_run(
                        project_id=project_id,
                        contract_id=int(contract_id),
                        generation_run_id=generation_run_id,
                        connection_id=int(ai_connection_id),
                        provider_type=str(ai_connection["provider_type"]),
                        model_identifier=ai_model,
                        run_type="artifact_revision",
                        prompt_version=ARTIFACT_REVISION_PROMPT_VERSION,
                        request_summary={
                            "artifactCount": len(editable),
                            "paths": [
                                item["relativePath"]
                                for item in editable
                            ],
                            "sourceFileCount": len(source_files),
                        },
                        created_by=int(generation["created_by"]),
                    )

                    try:
                        revision_result = execute_artifact_revision(
                            ai_run_id=int(revision_run["id"]),
                            connection_id=int(ai_connection_id),
                            model_identifier=ai_model,
                            payload=revision_payload,
                            temperature=0.05,
                        )
                    except AiProviderError as error:
                        add_generation_event(
                            generation_run_id=generation_run_id,
                            level="warning",
                            step="ai_artifact_revision",
                            message=(
                                "La révision IA des fichiers a échoué. "
                                "Les templates sûrs de SApixi sont conservés."
                            ),
                            details={
                                "fallback": True,
                                "errorCode": error.code,
                                "errorMessage": str(error),
                                "model": ai_model,
                            },
                        )
                    else:
                        artifacts, revision_report = apply_ai_artifact_revision(
                            artifacts=artifacts,
                            revision=revision_result.output,
                        )

                        summary["aiArtifactRevision"] = {
                            **revision_report,
                            "model": revision_result.model_identifier,
                            "latencyMs": revision_result.latency_ms,
                        }

                        validation_counts = {
                            "passed": 0,
                            "warning": 0,
                            "failed": 0,
                            "pending": 0,
                        }
                        for artifact_item in artifacts:
                            status = str(
                                artifact_item.get("validation_status") or "pending"
                            )
                            validation_counts[status] = (
                                validation_counts.get(status, 0) + 1
                            )

                        summary["validationCounts"] = validation_counts
                        summary["readyForReview"] = (
                            validation_counts.get("failed", 0) == 0
                        )

                        if revision_report.get("rejected"):
                            add_generation_event(
                                generation_run_id=generation_run_id,
                                level="warning",
                                step="ai_artifact_revision",
                                message=(
                                    "Qwen a proposé des fichiers, mais leur "
                                    "validation a échoué. SApixi a restauré "
                                    "automatiquement les templates sûrs."
                                ),
                                details=revision_report,
                            )
                        elif revision_report.get("applied"):
                            add_generation_event(
                                generation_run_id=generation_run_id,
                                level="success",
                                step="ai_artifact_revision",
                                message=(
                                    "Les artefacts proposés par Qwen ont été "
                                    "validés et intégrés à la génération."
                                ),
                                details=revision_report,
                            )
                        else:
                            add_generation_event(
                                generation_run_id=generation_run_id,
                                level="info",
                                step="ai_artifact_revision",
                                message=(
                                    "Qwen n'a proposé aucune modification utile. "
                                    "Les templates SApixi sont conservés."
                                ),
                                details=revision_report,
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
    Construit le contexte code transmis à Qwen.

    V1 : on transmet maintenant du vrai code applicatif en plus des fichiers
    de build/configuration. Les fichiers sont classés par pertinence afin de
    ne pas remplir le contexte avec des modules secondaires avant les routes,
    entrypoints, health checks et fichiers de configuration.
    """

    policies = contract.get("policies")
    maximum_bytes = 200_000

    if isinstance(policies, dict):
        try:
            maximum_bytes = int(
                policies.get("maximumAiContextBytes", maximum_bytes)
            )
        except (TypeError, ValueError):
            maximum_bytes = 200_000

    configured_cap = int(
        current_app.config.get(
            "AI_MAX_SOURCE_CONTEXT_BYTES",
            60_000,
        )
    )

    maximum_bytes = min(
        500_000,
        max(8_000, maximum_bytes),
        max(8_000, configured_cap),
    )

    source_root = source_root.resolve()
    candidate_roots: list[Path] = [source_root]

    components = contract.get("components")
    if isinstance(components, list):
        for component in components:
            if not isinstance(component, dict):
                continue

            candidate = (
                source_root / str(component.get("rootPath") or ".")
            ).resolve()

            try:
                candidate.relative_to(source_root)
            except ValueError:
                continue

            if candidate.is_dir() and candidate not in candidate_roots:
                candidate_roots.append(candidate)

    candidates: dict[Path, tuple[int, str]] = {}

    for root in candidate_roots:
        for path in root.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue

            try:
                relative = path.resolve().relative_to(source_root)
            except ValueError:
                continue

            if any(part in SOURCE_EXCLUDED_PARTS for part in relative.parts):
                continue

            if (
                path.name in SENSITIVE_SOURCE_NAMES
                or path.name.startswith(".env")
            ):
                continue

            suffix = path.suffix.lower()
            if (
                path.name not in SOURCE_FILE_NAMES
                and suffix not in SOURCE_FILE_SUFFIXES
            ):
                continue

            try:
                size = path.stat().st_size
            except OSError:
                continue

            # Les gros fichiers générés/minifiés ne sont pas utiles au LLM.
            if size <= 0 or size > 35_000:
                continue

            lower_name = path.name.lower()
            relative_text = relative.as_posix().lower()

            score = 100
            if path.name in SOURCE_FILE_NAMES:
                score -= 50
            if any(term in lower_name for term in SOURCE_PRIORITY_TERMS):
                score -= 35
            if any(
                token in relative_text
                for token in (
                    "/routes", "/controllers", "/api/", "/health",
                    "/config", "/settings", "/worker", "/tasks",
                )
            ):
                score -= 20
            score += min(len(relative.parts), 10)

            candidates[path.resolve()] = (score, relative.as_posix())

    ordered = sorted(
        candidates,
        key=lambda path: (
            candidates[path][0],
            candidates[path][1],
        ),
    )

    result: list[dict[str, str]] = []
    used_bytes = 0

    for path in ordered:
        if len(result) >= 80:
            break

        try:
            content = path.read_text(
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            continue

        content_bytes = len(content.encode("utf-8"))
        if used_bytes + content_bytes > maximum_bytes:
            continue

        used_bytes += content_bytes
        result.append(
            {
                "path": candidates[path][1],
                "content": content,
            }
        )

    return result

