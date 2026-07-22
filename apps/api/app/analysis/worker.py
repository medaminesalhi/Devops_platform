from __future__ import annotations

from concurrent.futures import (
    ThreadPoolExecutor,
)

from flask import Flask

from app.analysis.detectors import (
    analyze_repository,
)

from app.analysis.git_workspace import (
    GitWorkspaceError,
    git_workspace_manager,
)

from app.analysis.repository import (
    add_analysis_event,
    complete_analysis,
    fail_analysis,
    find_analysis_run,
    find_project_source,
    replace_analysis_components,
    update_analysis_progress,
)


analysis_executor = ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix=
        "piximind-analysis",
)


def submit_analysis(
    *,
    app: Flask,
    analysis_run_id: int,
) -> None:
    analysis_executor.submit(
        run_analysis_job,
        app,
        analysis_run_id,
    )


def run_analysis_job(
    app: Flask,
    analysis_run_id: int,
) -> None:
    with app.app_context():
        analysis_run = find_analysis_run(
            analysis_run_id
        )

        if analysis_run is None:
            return

        project_id = int(
            analysis_run["project_id"]
        )

        try:
            project = find_project_source(
                project_id
            )

            if project is None:
                raise RuntimeError(
                    "Le projet est introuvable."
                )

            update_analysis_progress(
                analysis_run_id=
                    analysis_run_id,

                status="preparing",
                progress=5,
                current_step="preparing",
            )

            add_analysis_event(
                analysis_run_id=
                    analysis_run_id,

                level="info",
                step="preparing",

                message=(
                    "Préparation du workspace "
                    "temporaire."
                ),
            )

            update_analysis_progress(
                analysis_run_id=
                    analysis_run_id,

                status="cloning",
                progress=15,
                current_step="cloning",
            )

            add_analysis_event(
                analysis_run_id=
                    analysis_run_id,

                level="info",
                step="credential",

                message=(
                    "Chargement sécurisé "
                    "du credential Git."
                ),
            )

            with git_workspace_manager.checkout(
                project=project,

                commit_policy=
                    analysis_run[
                        "commit_policy"
                    ],
            ) as checkout:
                update_analysis_progress(
                    analysis_run_id=
                        analysis_run_id,

                    status="cloning",
                    progress=40,
                    current_step=
                        "checkout_completed",

                    branch_head_sha=
                        checkout.branch_head_sha,

                    analyzed_commit_sha=
                        checkout.analyzed_commit_sha,
                )

                add_analysis_event(
                    analysis_run_id=
                        analysis_run_id,

                    level="success",
                    step="clone",

                    message=(
                        "Repository téléchargé "
                        "dans le workspace temporaire."
                    ),

                    details={
                        "branchHeadSha":
                            checkout.branch_head_sha,

                        "analyzedCommitSha":
                            checkout.analyzed_commit_sha,

                        "branchChanged":
                            checkout.branch_changed,
                    },
                )

                if checkout.branch_changed:
                    add_analysis_event(
                        analysis_run_id=
                            analysis_run_id,

                        level="warning",
                        step="commit",

                        message=(
                            "La branche contient un commit "
                            "différent de celui validé "
                            "pendant la phase 1."
                        ),

                        details={
                            "policy":
                                analysis_run[
                                    "commit_policy"
                                ],

                            "validatedCommit":
                                project[
                                    "last_source_commit_sha"
                                ],

                            "currentBranchHead":
                                checkout.branch_head_sha,

                            "analyzedCommit":
                                checkout.analyzed_commit_sha,
                        },
                    )

                update_analysis_progress(
                    analysis_run_id=
                        analysis_run_id,

                    status="analyzing",
                    progress=50,
                    current_step="inventory",
                )

                add_analysis_event(
                    analysis_run_id=
                        analysis_run_id,

                    level="info",
                    step="inventory",

                    message=(
                        "Création de l'inventaire "
                        "des fichiers."
                    ),
                )

                report = analyze_repository(
                    source_root=
                        checkout.source_path,

                    selected_subdirectory=
                        project[
                            "source_subdirectory"
                        ],

                    max_files=int(
                        app.config.get(
                            "ANALYSIS_MAX_FILES",
                            20000,
                        )
                    ),

                    max_file_size_bytes=int(
                        app.config.get(
                            "ANALYSIS_MAX_FILE_SIZE_BYTES",
                            2_000_000,
                        )
                    ),
                )

                update_analysis_progress(
                    analysis_run_id=
                        analysis_run_id,

                    status="analyzing",
                    progress=75,
                    current_step=
                        "component_detection",
                )

                add_analysis_event(
                    analysis_run_id=
                        analysis_run_id,

                    level="info",
                    step="detection",

                    message=(
                        f"{len(report.components)} "
                        "composant(s) détecté(s)."
                    ),
                )

                component_dicts = [
                    component.to_dict()
                    for component
                    in report.components
                ]

                replace_analysis_components(
                    project_id=project_id,

                    analysis_run_id=
                        analysis_run_id,

                    components=
                        component_dicts,
                )

                update_analysis_progress(
                    analysis_run_id=
                        analysis_run_id,

                    status="analyzing",
                    progress=90,
                    current_step=
                        "report_generation",
                )

                for warning in (
                    report.summary.get(
                        "warnings"
                    )
                    or []
                ):
                    add_analysis_event(
                        analysis_run_id=
                            analysis_run_id,

                        level="warning",
                        step="report",

                        message=warning,
                    )

                complete_analysis(
                    analysis_run_id=
                        analysis_run_id,

                    project_id=
                        project_id,

                    branch_head_sha=
                        checkout.branch_head_sha,

                    analyzed_commit_sha=
                        checkout.analyzed_commit_sha,

                    summary=
                        report.summary,
                )

                add_analysis_event(
                    analysis_run_id=
                        analysis_run_id,

                    level="success",
                    step="completed",

                    message=(
                        "Analyse statique terminée "
                        "avec succès."
                    ),

                    details={
                        "componentCount":
                            len(
                                report.components
                            ),

                        "phase3Ready":
                            report.summary.get(
                                "phase3Ready"
                            ),
                    },
                )

        except GitWorkspaceError as error:
            fail_analysis(
                analysis_run_id=
                    analysis_run_id,

                project_id=
                    project_id,

                error_code=
                    error.code,

                error_message=
                    error.message,
            )

            add_analysis_event(
                analysis_run_id=
                    analysis_run_id,

                level="error",
                step="git",

                message=
                    error.message,

                details={
                    "code":
                        error.code,
                },
            )

        except Exception as error:
            app.logger.exception(
                (
                    "Erreur pendant l'analyse "
                    "du projet %s."
                ),
                project_id,
            )

            fail_analysis(
                analysis_run_id=
                    analysis_run_id,

                project_id=
                    project_id,

                error_code=
                    "ANALYSIS_FAILED",

                error_message=(
                    "L'analyse du repository "
                    "a échoué."
                ),
            )

            add_analysis_event(
                analysis_run_id=
                    analysis_run_id,

                level="error",
                step="analysis",

                message=(
                    "L'analyse du repository "
                    "a échoué."
                ),

                details={
                    "exceptionType":
                        type(error).__name__,
                },
            )