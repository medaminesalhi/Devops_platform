from __future__ import annotations

from typing import Any

from flask import current_app

from app.analysis.git_workspace import (
    GitWorkspaceError,
    git_workspace_manager,
)
from app.analysis.repository import (
    add_analysis_event,
    confirm_analysis,
    create_analysis_run,
    find_active_analysis,
    find_analysis_for_project,
    find_latest_analysis,
    find_project_source,
    list_analysis_components,
    list_analysis_events,
    update_component,
)
from app.analysis.worker import submit_analysis


class AnalysisServiceError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        http_status: int = 400,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


ANALYSIS_ROLES = {
    "admin",
    "administrator",
    "devops",
    "developer",
}


def ensure_analysis_role(roles: set[str]) -> None:
    if not roles.intersection(ANALYSIS_ROLES):
        raise AnalysisServiceError(
            "ANALYSIS_FORBIDDEN",
            "Votre rôle ne permet pas de lancer ou modifier une analyse.",
            403,
        )


def list_project_source_commits(
    *,
    project_id: int,
    limit: int = 30,
) -> dict[str, Any]:
    project = find_project_source(project_id)
    if project is None:
        raise AnalysisServiceError(
            "PROJECT_NOT_FOUND",
            "Le projet est introuvable.",
            404,
        )

    source_type = str(project.get("source_type") or "git").lower()
    if source_type == "zip":
        version = str(project.get("archive_sha256") or "").strip().lower()
        commits = []
        if version:
            commits.append(
                {
                    "sha": version,
                    "shortSha": version[:12],
                    "message": "Archive ZIP validée",
                    "authorName": None,
                    "authorEmail": None,
                    "committedAt": None,
                    "isHead": True,
                }
            )
        return {
            "sourceType": "zip",
            "branch": None,
            "head": version or None,
            "commits": commits,
        }

    try:
        history = git_workspace_manager.list_branch_commits(
            project=project,
            limit=limit,
        )
    except GitWorkspaceError as error:
        raise AnalysisServiceError(
            error.code,
            error.message,
            400,
        ) from error

    return {
        "sourceType": "git",
        "branch": history["branch"],
        "head": history["head"],
        "commits": history["commits"],
    }


def start_project_analysis(
    *,
    project_id: int,
    user_id: int,
    roles: set[str],
    commit_policy: str,
    requested_commit_sha: str | None = None,
) -> dict[str, Any]:
    ensure_analysis_role(roles)

    project = find_project_source(project_id)

    if project is None:
        raise AnalysisServiceError(
            "PROJECT_NOT_FOUND",
            "Le projet est introuvable.",
            404,
        )

    if project["source_status"] != "valid":
        raise AnalysisServiceError(
            "PROJECT_SOURCE_NOT_VALIDATED",
            "La source doit être validée avant l'analyse.",
            409,
        )

    source_type = str(
        project.get("source_type") or "git"
    ).lower()

    if source_type == "zip":
        requested_version = project.get("archive_sha256")
        commit_policy = "validated"

        if not project.get("archive_storage_path"):
            raise AnalysisServiceError(
                "PROJECT_ARCHIVE_MISSING",
                "L'archive ZIP du projet est introuvable.",
                409,
            )
    else:
        requested_version = str(requested_commit_sha or "").strip().lower() or None
        commit_policy = "validated" if requested_version else "latest"

        if not project.get("repository_url"):
            raise AnalysisServiceError(
                "PROJECT_REPOSITORY_MISSING",
                "L'URL du repository est absente.",
                409,
            )

    active_analysis = find_active_analysis(project_id)

    if active_analysis is not None:
        raise AnalysisServiceError(
            "ANALYSIS_ALREADY_RUNNING",
            "Une analyse est déjà en cours pour ce projet.",
            409,
        )

    analysis_run = create_analysis_run(
        project_id=project_id,
        commit_policy=commit_policy,
        requested_commit_sha=requested_version,
        selected_subdirectory=project.get("source_subdirectory"),
        created_by=user_id,
    )

    add_analysis_event(
        analysis_run_id=int(analysis_run["id"]),
        level="info",
        step="pending",
        message=(
            f"Analyse du commit {requested_version[:12]} ajoutée à la file d'exécution."
            if requested_version and source_type == "git"
            else "Analyse ajoutée à la file d'exécution."
        ),
        details={
            "sourceType": source_type,
            "requestedVersion": requested_version,
            "automaticVersionSelection": requested_version is None,
        },
    )

    submit_analysis(
        app=current_app._get_current_object(),
        analysis_run_id=int(analysis_run["id"]),
    )

    return analysis_run


def get_latest_project_analysis(
    project_id: int,
) -> dict[str, Any]:
    analysis = find_latest_analysis(project_id)

    if analysis is None:
        raise AnalysisServiceError(
            "ANALYSIS_NOT_FOUND",
            "Aucune analyse n'existe pour ce projet.",
            404,
        )

    return enrich_analysis(analysis)


def get_project_analysis(
    *,
    project_id: int,
    analysis_run_id: int,
) -> dict[str, Any]:
    analysis = find_analysis_for_project(
        project_id=project_id,
        analysis_run_id=analysis_run_id,
    )

    if analysis is None:
        raise AnalysisServiceError(
            "ANALYSIS_NOT_FOUND",
            "L'analyse est introuvable.",
            404,
        )

    return enrich_analysis(analysis)


def get_project_analysis_events(
    *,
    project_id: int,
    analysis_run_id: int,
    after_id: int,
) -> list[dict[str, Any]]:
    analysis = find_analysis_for_project(
        project_id=project_id,
        analysis_run_id=analysis_run_id,
    )

    if analysis is None:
        raise AnalysisServiceError(
            "ANALYSIS_NOT_FOUND",
            "L'analyse est introuvable.",
            404,
        )

    return list_analysis_events(
        analysis_run_id=analysis_run_id,
        after_id=after_id,
    )


def update_analysis_component(
    *,
    project_id: int,
    analysis_run_id: int,
    component_id: int,
    roles: set[str],
    changes: dict[str, Any],
) -> dict[str, Any]:
    ensure_analysis_role(roles)

    analysis = find_analysis_for_project(
        project_id=project_id,
        analysis_run_id=analysis_run_id,
    )

    if analysis is None:
        raise AnalysisServiceError(
            "ANALYSIS_NOT_FOUND",
            "L'analyse est introuvable.",
            404,
        )

    if analysis["status"] != "completed":
        raise AnalysisServiceError(
            "ANALYSIS_NOT_EDITABLE",
            (
                "Les composants sont modifiables uniquement après "
                "une analyse terminée et avant sa confirmation."
            ),
            409,
        )

    component = update_component(
        component_id=component_id,
        analysis_run_id=analysis_run_id,
        changes=changes,
    )

    if component is None:
        raise AnalysisServiceError(
            "COMPONENT_NOT_FOUND",
            "Le composant est introuvable.",
            404,
        )

    return component


def confirm_project_analysis(
    *,
    project_id: int,
    analysis_run_id: int,
    user_id: int,
    roles: set[str],
) -> None:
    ensure_analysis_role(roles)

    analysis = find_analysis_for_project(
        project_id=project_id,
        analysis_run_id=analysis_run_id,
    )

    if analysis is None:
        raise AnalysisServiceError(
            "ANALYSIS_NOT_FOUND",
            "L'analyse est introuvable.",
            404,
        )

    if analysis["status"] != "completed":
        raise AnalysisServiceError(
            "ANALYSIS_NOT_CONFIRMABLE",
            "Seule une analyse terminée peut être confirmée.",
            409,
        )

    components = list_analysis_components(analysis_run_id)

    if not any(component["deployable"] for component in components):
        raise AnalysisServiceError(
            "NO_DEPLOYABLE_COMPONENT",
            "Au moins un composant doit être marqué comme déployable.",
            409,
        )

    confirmed = confirm_analysis(
        project_id=project_id,
        analysis_run_id=analysis_run_id,
        user_id=user_id,
    )

    if not confirmed:
        raise AnalysisServiceError(
            "ANALYSIS_CONFIRMATION_CONFLICT",
            "L'analyse ne peut plus être confirmée.",
            409,
        )

    add_analysis_event(
        analysis_run_id=analysis_run_id,
        level="success",
        step="confirmed",
        message="Analyse confirmée par l'utilisateur.",
        details={"confirmedBy": user_id},
    )


def enrich_analysis(
    analysis: dict[str, Any],
) -> dict[str, Any]:
    return {
        **analysis,
        "components": list_analysis_components(
            int(analysis["id"])
        ),
    }
