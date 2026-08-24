from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from flask import Flask

from app.analysis.detectors import analyze_repository
from app.analysis.evidence import enrich_analysis_report
from app.analysis.repository import (
    add_analysis_event,
    complete_analysis,
    fail_analysis,
    find_analysis_run,
    find_project_source,
    replace_analysis_components,
    update_analysis_progress,
)
from app.analysis.source_workspace import (
    SourceWorkspaceError,
    source_workspace_manager,
)


analysis_executor = ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="piximind-analysis",
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
            project = find_project_source(project_id)

            if project is None:
                raise RuntimeError(
                    "Le projet est introuvable."
                )

            set_step(
                analysis_run_id=analysis_run_id,
                status="preparing",
                progress=8,
                step="preparing_source",
                message="Préparation de la source du projet.",
            )

            with source_workspace_manager.prepare(
                project,
                commit_policy=str(analysis_run.get("commit_policy") or "latest"),
                requested_commit_sha=analysis_run.get("requested_commit_sha"),
            ) as prepared_source:
                source_action = (
                    "Extraction de l'archive terminée."
                    if prepared_source.source_type == "zip"
                    else "Téléchargement du repository terminé."
                )

                update_analysis_progress(
                    analysis_run_id=analysis_run_id,
                    status="cloning",
                    progress=30,
                    current_step="source_ready",
                    branch_head_sha=(
                        prepared_source.branch_head
                        or prepared_source.version
                    ),
                    analyzed_commit_sha=(
                        prepared_source.version
                    ),
                )

                add_analysis_event(
                    analysis_run_id=analysis_run_id,
                    level="success",
                    step="source_ready",
                    message=source_action,
                    details={
                        "sourceType": prepared_source.source_type,
                        "version": prepared_source.version,
                        "previousVersion": prepared_source.previous_version,
                        "sourceChanged": prepared_source.source_changed,
                    },
                )

                set_step(
                    analysis_run_id=analysis_run_id,
                    status="analyzing",
                    progress=45,
                    step="inventory",
                    message="Inventaire sécurisé des fichiers.",
                )

                report = analyze_repository(
                    source_root=prepared_source.source_path,
                    selected_subdirectory=(
                        project.get("source_subdirectory")
                    ),
                    max_files=int(
                        app.config.get(
                            "ANALYSIS_MAX_FILES",
                            20_000,
                        )
                    ),
                    max_file_size_bytes=int(
                        app.config.get(
                            "ANALYSIS_MAX_FILE_SIZE_BYTES",
                            2_000_000,
                        )
                    ),
                )

                set_step(
                    analysis_run_id=analysis_run_id,
                    status="analyzing",
                    progress=68,
                    step="technology_detection",
                    message="Détection des composants et des technologies.",
                )

                report = enrich_analysis_report(
                    source_root=prepared_source.source_path,
                    selected_subdirectory=(
                        project.get("source_subdirectory")
                    ),
                    report=report,
                )

                set_step(
                    analysis_run_id=analysis_run_id,
                    status="analyzing",
                    progress=84,
                    step="deployment_analysis",
                    message=(
                        "Analyse des Dockerfiles, de Helm, "
                        "de Kubernetes et d'Argo CD."
                    ),
                )

                report.summary["source"] = {
                    "type": prepared_source.source_type,
                    "displayName": prepared_source.display_name,
                    "version": prepared_source.version,
                    "shortVersion": prepared_source.version[:12],
                    "previousVersion": prepared_source.previous_version,
                    "sourceChanged": prepared_source.source_changed,
                    "branch": (
                        project.get("default_branch")
                        if prepared_source.source_type == "git"
                        else None
                    ),
                }

                report.summary["project"] = {
                    "operationMode": (
                        project.get("operation_mode")
                        or "new_application"
                    ),
                    "environment": {
                        "id": project.get("default_environment_id"),
                        "name": project.get("environment_name"),
                        "namespace": project.get("environment_namespace"),
                    },
                }

                set_step(
                    analysis_run_id=analysis_run_id,
                    status="analyzing",
                    progress=94,
                    step="report_generation",
                    message="Génération du rapport d'analyse.",
                )

                component_dicts = [
                    component.to_dict()
                    for component in report.components
                ]

                replace_analysis_components(
                    project_id=project_id,
                    analysis_run_id=analysis_run_id,
                    components=component_dicts,
                )

                for warning in report.summary.get(
                    "warnings",
                    [],
                ):
                    add_analysis_event(
                        analysis_run_id=analysis_run_id,
                        level="warning",
                        step="report_generation",
                        message=warning,
                    )

                complete_analysis(
                    analysis_run_id=analysis_run_id,
                    project_id=project_id,
                    source_type=prepared_source.source_type,
                    branch_head_sha=(
                        prepared_source.branch_head
                        or prepared_source.version
                    ),
                    analyzed_commit_sha=prepared_source.version,
                    summary=report.summary,
                )

                add_analysis_event(
                    analysis_run_id=analysis_run_id,
                    level="success",
                    step="completed",
                    message="Analyse du projet terminée.",
                    details={
                        "componentCount": len(report.components),
                        "globalConfidence": report.summary.get(
                            "globalConfidence",
                            0,
                        ),
                    },
                )

        except SourceWorkspaceError as error:
            record_failure(
                analysis_run_id=analysis_run_id,
                project_id=project_id,
                code=error.code,
                message=error.message,
                step="source",
            )

        except ValueError as error:
            record_failure(
                analysis_run_id=analysis_run_id,
                project_id=project_id,
                code="ANALYSIS_CONFIGURATION_INVALID",
                message=str(error),
                step="analysis",
            )

        except Exception as error:
            app.logger.exception(
                "Erreur pendant l'analyse du projet %s.",
                project_id,
            )

            record_failure(
                analysis_run_id=analysis_run_id,
                project_id=project_id,
                code="ANALYSIS_FAILED",
                message="L'analyse du projet a échoué.",
                step="analysis",
                details={
                    "exceptionType": type(error).__name__,
                },
            )


def set_step(
    *,
    analysis_run_id: int,
    status: str,
    progress: int,
    step: str,
    message: str,
) -> None:
    update_analysis_progress(
        analysis_run_id=analysis_run_id,
        status=status,
        progress=progress,
        current_step=step,
    )

    add_analysis_event(
        analysis_run_id=analysis_run_id,
        level="info",
        step=step,
        message=message,
    )


def record_failure(
    *,
    analysis_run_id: int,
    project_id: int,
    code: str,
    message: str,
    step: str,
    details: dict | None = None,
) -> None:
    fail_analysis(
        analysis_run_id=analysis_run_id,
        project_id=project_id,
        error_code=code,
        error_message=message,
    )

    add_analysis_event(
        analysis_run_id=analysis_run_id,
        level="error",
        step=step,
        message=message,
        details={
            "code": code,
            **(details or {}),
        },
    )
