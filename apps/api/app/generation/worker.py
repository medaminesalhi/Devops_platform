from __future__ import annotations

from concurrent.futures import (
    ThreadPoolExecutor,
)

from flask import Flask

from app.generation.generators import (
    build_generation_plan,
)

from app.generation.repository import (
    add_generation_event,
    complete_generation,
    fail_generation,
    find_generation_context,
    find_generation_run,
    list_confirmed_components,
    replace_generation_artifacts,
    update_generation_progress,
)

from app.generation.workspace import (
    GenerationWorkspaceError,
    generation_workspace_manager,
)


generation_executor = ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix=
        "piximind-generation",
)


def submit_generation(
    *,
    app: Flask,
    generation_run_id: int,
) -> None:
    generation_executor.submit(
        run_generation_job,
        app,
        generation_run_id,
    )


def run_generation_job(
    app: Flask,
    generation_run_id: int,
) -> None:
    with app.app_context():
        generation_run = (
            find_generation_run(
                generation_run_id
            )
        )

        if generation_run is None:
            return

        project_id = int(
            generation_run[
                "project_id"
            ]
        )

        try:
            context = (
                find_generation_context(
                    project_id
                )
            )

            if context is None:
                raise RuntimeError(
                    (
                        "Le projet est "
                        "introuvable."
                    )
                )

            analysis_run_id = int(
                generation_run[
                    "analysis_run_id"
                ]
            )

            components = (
                list_confirmed_components(
                    analysis_run_id
                )
            )

            if not components:
                raise ValueError(
                    (
                        "L'analyse confirmée "
                        "ne contient aucun "
                        "composant déployable."
                    )
                )

            set_step(
                generation_run_id=
                    generation_run_id,

                project_id=
                    project_id,

                progress=8,

                step=
                    "loading_analysis",

                message=(
                    "Chargement de "
                    "l'analyse confirmée."
                ),
            )

            set_step(
                generation_run_id=
                    generation_run_id,

                project_id=
                    project_id,

                progress=18,

                step=
                    "preparing_source",

                message=(
                    "Préparation de la "
                    "version exacte du code."
                ),
            )

            with (
                generation_workspace_manager
                .prepare(context)
            ) as prepared_source:
                add_generation_event(
                    generation_run_id=
                        generation_run_id,

                    level="success",

                    step="source_ready",

                    message=(
                        "Version du code chargée "
                        "dans le workspace "
                        "temporaire."
                    ),

                    details={
                        "sourceType":
                            prepared_source
                            .source_type,

                        "version":
                            prepared_source
                            .version,
                    },
                )

                set_step(
                    generation_run_id=
                        generation_run_id,

                    project_id=
                        project_id,

                    progress=35,

                    step=
                        "docker_generation",

                    message=(
                        "Préparation des "
                        "Dockerfiles et des "
                        ".dockerignore."
                    ),
                )

                set_step(
                    generation_run_id=
                        generation_run_id,

                    project_id=
                        project_id,

                    progress=58,

                    step=
                        "helm_generation",

                    message=(
                        "Génération des "
                        "charts Helm."
                    ),
                )

                (
                    artifacts,
                    summary,
                ) = build_generation_plan(
                    source_root=
                        prepared_source
                        .source_path,

                    context=
                        context,

                    components=
                        components,
                )

                set_step(
                    generation_run_id=
                        generation_run_id,

                    project_id=
                        project_id,

                    progress=78,

                    step=
                        "gitops_generation",

                    message=(
                        "Préparation de la "
                        "structure GitOps."
                    ),
                )

                set_step(
                    generation_run_id=
                        generation_run_id,

                    project_id=
                        project_id,

                    progress=90,

                    step=
                        "argocd_generation",

                    message=(
                        "Préparation des "
                        "Applications Argo CD."
                    ),
                )

                replace_generation_artifacts(
                    generation_run_id=
                        generation_run_id,

                    project_id=
                        project_id,

                    artifacts=
                        artifacts,
                )

                set_step(
                    generation_run_id=
                        generation_run_id,

                    project_id=
                        project_id,

                    progress=97,

                    step=
                        "report_generation",

                    message=(
                        "Construction du rapport "
                        "de génération."
                    ),
                )

                for warning in summary.get(
                    "warnings",
                    [],
                ):
                    add_generation_event(
                        generation_run_id=
                            generation_run_id,

                        level=
                            "warning",

                        step=
                            "report_generation",

                        message=
                            str(warning),
                    )

                complete_generation(
                    generation_run_id=
                        generation_run_id,

                    project_id=
                        project_id,

                    summary=
                        summary,
                )

                add_generation_event(
                    generation_run_id=
                        generation_run_id,

                    level="success",

                    step="completed",

                    message=(
                        "Génération des "
                        "artefacts terminée."
                    ),

                    details={
                        "artifactCount":
                            summary[
                                "artifactCount"
                            ],

                        "componentCount":
                            summary[
                                "componentCount"
                            ],
                    },
                )

        except (
            GenerationWorkspaceError
        ) as error:
            record_failure(
                generation_run_id=
                    generation_run_id,

                project_id=
                    project_id,

                code=
                    error.code,

                message=
                    error.message,

                step="source",
            )

        except ValueError as error:
            record_failure(
                generation_run_id=
                    generation_run_id,

                project_id=
                    project_id,

                code=(
                    "GENERATION_"
                    "CONFIGURATION_INVALID"
                ),

                message=
                    str(error),

                step=
                    "generation",
            )

        except Exception as error:
            app.logger.exception(
                (
                    "Erreur pendant la "
                    "génération du projet %s."
                ),
                project_id,
            )

            record_failure(
                generation_run_id=
                    generation_run_id,

                project_id=
                    project_id,

                code=
                    "GENERATION_FAILED",

                message=(
                    "La génération des "
                    "artefacts a échoué."
                ),

                step=
                    "generation",

                details={
                    "exceptionType":
                        type(error).__name__,
                },
            )


def set_step(
    *,
    generation_run_id: int,
    project_id: int,
    progress: int,
    step: str,
    message: str,
) -> None:
    update_generation_progress(
        generation_run_id=
            generation_run_id,

        project_id=
            project_id,

        progress=
            progress,

        current_step=
            step,
    )

    add_generation_event(
        generation_run_id=
            generation_run_id,

        level=
            "info",

        step=
            step,

        message=
            message,
    )


def record_failure(
    *,
    generation_run_id: int,
    project_id: int,
    code: str,
    message: str,
    step: str,
    details: dict | None = None,
) -> None:
    fail_generation(
        generation_run_id=
            generation_run_id,

        project_id=
            project_id,

        error_code=
            code,

        error_message=
            message,
    )

    add_generation_event(
        generation_run_id=
            generation_run_id,

        level=
            "error",

        step=
            step,

        message=
            message,

        details={
            "code": code,
            **(details or {}),
        },
    )