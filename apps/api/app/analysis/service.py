from __future__ import annotations

from typing import Any

from flask import (
    current_app,
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

from app.analysis.worker import (
    submit_analysis,
)


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


def ensure_analysis_role(
    roles: set[str],
) -> None:
    if not roles.intersection(
        ANALYSIS_ROLES
    ):
        raise AnalysisServiceError(
            "ANALYSIS_FORBIDDEN",
            (
                "Votre rôle ne permet pas "
                "de lancer ou modifier une analyse."
            ),
            403,
        )


def start_project_analysis(
    *,
    project_id: int,
    user_id: int,
    roles: set[str],
    commit_policy: str,
) -> dict[str, Any]:
    ensure_analysis_role(
        roles
    )

    project = find_project_source(
        project_id
    )

    if project is None:
        raise AnalysisServiceError(
            "PROJECT_NOT_FOUND",
            "Le projet est introuvable.",
            404,
        )

    if project["source_status"] != "valid":
        raise AnalysisServiceError(
            "PROJECT_SOURCE_NOT_VALIDATED",
            (
                "La source du projet doit être "
                "validée avant l'analyse."
            ),
            409,
        )

    if not project[
        "last_source_commit_sha"
    ]:
        raise AnalysisServiceError(
            "PROJECT_COMMIT_MISSING",
            (
                "Aucun commit validé n'est "
                "enregistré pour ce projet."
            ),
            409,
        )

    active_analysis = (
        find_active_analysis(
            project_id
        )
    )

    if active_analysis is not None:
        raise AnalysisServiceError(
            "ANALYSIS_ALREADY_RUNNING",
            (
                "Une analyse est déjà en cours "
                "pour ce projet."
            ),
            409,
        )

    analysis_run = create_analysis_run(
        project_id=project_id,

        commit_policy=
            commit_policy,

        requested_commit_sha=
            project[
                "last_source_commit_sha"
            ],

        selected_subdirectory=
            project[
                "source_subdirectory"
            ],

        created_by=
            user_id,
    )

    add_analysis_event(
        analysis_run_id=
            int(analysis_run["id"]),

        level="info",
        step="pending",

        message=(
            "Analyse ajoutée à la file "
            "d'exécution."
        ),

        details={
            "commitPolicy":
                commit_policy,

            "requestedCommitSha":
                project[
                    "last_source_commit_sha"
                ],

            "selectedSubdirectory":
                project[
                    "source_subdirectory"
                ],
        },
    )

    submit_analysis(
        app=current_app
            ._get_current_object(),

        analysis_run_id=
            int(analysis_run["id"]),
    )

    return analysis_run


def get_latest_project_analysis(
    project_id: int,
) -> dict[str, Any]:
    analysis = find_latest_analysis(
        project_id
    )

    if analysis is None:
        raise AnalysisServiceError(
            "ANALYSIS_NOT_FOUND",
            (
                "Aucune analyse n'existe "
                "pour ce projet."
            ),
            404,
        )

    return enrich_analysis(
        analysis
    )


def get_project_analysis(
    *,
    project_id: int,
    analysis_run_id: int,
) -> dict[str, Any]:
    analysis = find_analysis_for_project(
        project_id=project_id,
        analysis_run_id=
            analysis_run_id,
    )

    if analysis is None:
        raise AnalysisServiceError(
            "ANALYSIS_NOT_FOUND",
            "L'analyse est introuvable.",
            404,
        )

    return enrich_analysis(
        analysis
    )


def get_project_analysis_events(
    *,
    project_id: int,
    analysis_run_id: int,
    after_id: int,
) -> list[dict[str, Any]]:
    analysis = find_analysis_for_project(
        project_id=project_id,
        analysis_run_id=
            analysis_run_id,
    )

    if analysis is None:
        raise AnalysisServiceError(
            "ANALYSIS_NOT_FOUND",
            "L'analyse est introuvable.",
            404,
        )

    return list_analysis_events(
        analysis_run_id=
            analysis_run_id,

        after_id=
            after_id,
    )


def update_analysis_component(
    *,
    project_id: int,
    analysis_run_id: int,
    component_id: int,
    roles: set[str],
    changes: dict[str, Any],
) -> dict[str, Any]:
    ensure_analysis_role(
        roles
    )

    analysis = find_analysis_for_project(
        project_id=project_id,
        analysis_run_id=
            analysis_run_id,
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
                "Les composants peuvent être "
                "modifiés uniquement après "
                "une analyse terminée et avant "
                "sa confirmation."
            ),
            409,
        )

    component = update_component(
        component_id=component_id,
        analysis_run_id=
            analysis_run_id,
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
    ensure_analysis_role(
        roles
    )

    analysis = find_analysis_for_project(
        project_id=project_id,
        analysis_run_id=
            analysis_run_id,
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
            (
                "Seule une analyse terminée "
                "peut être confirmée."
            ),
            409,
        )

    components = list_analysis_components(
        analysis_run_id
    )

    if not any(
        component["deployable"]
        for component in components
    ):
        raise AnalysisServiceError(
            "NO_DEPLOYABLE_COMPONENT",
            (
                "Au moins un composant déployable "
                "doit être confirmé."
            ),
            409,
        )

    confirm_analysis(
        project_id=project_id,
        analysis_run_id=
            analysis_run_id,
        user_id=user_id,
    )

    add_analysis_event(
        analysis_run_id=
            analysis_run_id,

        level="success",
        step="confirmed",

        message=(
            "Analyse confirmée par "
            "l'utilisateur."
        ),

        details={
            "confirmedBy":
                user_id,
        },
    )


def enrich_analysis(
    analysis: dict[str, Any],
) -> dict[str, Any]:
    return {
        **analysis,

        "components":
            list_analysis_components(
                int(analysis["id"])
            ),
    }