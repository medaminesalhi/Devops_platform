from __future__ import annotations

from typing import Any

from flask import current_app

from app.generation.repository import (
    add_generation_event,
    create_generation_run,
    find_active_generation,
    find_generation_artifact,
    find_generation_context,
    find_generation_for_project,
    find_latest_generation,
    list_generation_artifacts,
    list_generation_events,
)

from app.generation.worker import (
    submit_generation,
)


class GenerationServiceError(
    RuntimeError
):
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


GENERATION_ROLES = {
    "admin",
    "administrator",
    "devops",
    "developer",
}


def ensure_generation_role(
    roles: set[str],
) -> None:
    if not roles.intersection(
        GENERATION_ROLES
    ):
        raise GenerationServiceError(
            "GENERATION_FORBIDDEN",
            (
                "Votre rôle ne permet pas "
                "de lancer une génération."
            ),
            403,
        )


def start_project_generation(
    *,
    project_id: int,
    user_id: int,
    roles: set[str],
) -> dict[str, Any]:
    ensure_generation_role(
        roles
    )

    context = find_generation_context(
        project_id
    )

    if context is None:
        raise GenerationServiceError(
            "PROJECT_NOT_FOUND",
            "Le projet est introuvable.",
            404,
        )

    if not context.get(
        "confirmed_analysis_run_id"
    ):
        raise GenerationServiceError(
            "CONFIRMED_ANALYSIS_REQUIRED",
            (
                "Confirmez d'abord "
                "l'analyse de la phase 2."
            ),
            409,
        )

    if not context.get(
        "default_environment_id"
    ):
        raise GenerationServiceError(
            "PROJECT_ENVIRONMENT_REQUIRED",
            (
                "Le projet ne possède "
                "aucun environnement "
                "de déploiement."
            ),
            409,
        )

    active_generation = (
        find_active_generation(
            project_id
        )
    )

    if active_generation is not None:
        raise GenerationServiceError(
            "GENERATION_ALREADY_RUNNING",
            (
                "Une génération est déjà "
                "en cours pour ce projet."
            ),
            409,
        )

    generation_run = (
        create_generation_run(
            project_id=project_id,

            analysis_run_id=int(
                context[
                    "confirmed_analysis_run_id"
                ]
            ),

            environment_id=int(
                context[
                    "default_environment_id"
                ]
            ),

            created_by=user_id,
        )
    )

    add_generation_event(
        generation_run_id=int(
            generation_run["id"]
        ),

        level="info",

        step="pending",

        message=(
            "Génération ajoutée "
            "à la file d'exécution."
        ),

        details={
            "analysisRunId":
                context[
                    "confirmed_analysis_run_id"
                ],

            "version":
                context[
                    "confirmed_version"
                ],

            "environmentId":
                context[
                    "default_environment_id"
                ],
        },
    )

    submit_generation(
        app=current_app
            ._get_current_object(),

        generation_run_id=int(
            generation_run["id"]
        ),
    )

    return generation_run


def get_latest_project_generation(
    project_id: int,
) -> dict[str, Any]:
    generation = find_latest_generation(
        project_id
    )

    if generation is None:
        raise GenerationServiceError(
            "GENERATION_NOT_FOUND",
            (
                "Aucune génération "
                "n'existe pour ce projet."
            ),
            404,
        )

    return generation


def get_project_generation(
    *,
    project_id: int,
    generation_run_id: int,
) -> dict[str, Any]:
    generation = (
        find_generation_for_project(
            project_id=project_id,

            generation_run_id=
                generation_run_id,
        )
    )

    if generation is None:
        raise GenerationServiceError(
            "GENERATION_NOT_FOUND",
            (
                "La génération "
                "est introuvable."
            ),
            404,
        )

    return generation


def get_project_generation_events(
    *,
    project_id: int,
    generation_run_id: int,
    after_id: int,
) -> list[dict[str, Any]]:
    get_project_generation(
        project_id=project_id,

        generation_run_id=
            generation_run_id,
    )

    return list_generation_events(
        generation_run_id=
            generation_run_id,

        after_id=
            after_id,
    )


def get_project_generation_artifacts(
    *,
    project_id: int,
    generation_run_id: int,
) -> list[dict[str, Any]]:
    get_project_generation(
        project_id=project_id,

        generation_run_id=
            generation_run_id,
    )

    return list_generation_artifacts(
        generation_run_id
    )


def get_project_generation_artifact(
    *,
    project_id: int,
    generation_run_id: int,
    artifact_id: int,
) -> dict[str, Any]:
    get_project_generation(
        project_id=project_id,

        generation_run_id=
            generation_run_id,
    )

    artifact = (
        find_generation_artifact(
            generation_run_id=
                generation_run_id,

            artifact_id=
                artifact_id,
        )
    )

    if artifact is None:
        raise GenerationServiceError(
            "GENERATION_ARTIFACT_NOT_FOUND",
            (
                "L'artefact est "
                "introuvable."
            ),
            404,
        )

    return artifact