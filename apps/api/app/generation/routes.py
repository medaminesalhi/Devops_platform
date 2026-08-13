from __future__ import annotations

from typing import Any

from flask import (
    Blueprint,
    g,
    jsonify,
    request,
)

from app.auth.decorators import (
    require_auth,
    require_project_access,
)

from app.generation.service import (
    GenerationServiceError,
    get_latest_project_generation,
    get_project_generation,
    get_project_generation_artifact,
    get_project_generation_artifacts,
    get_project_generation_events,
    start_project_generation,
)


generation_blueprint = Blueprint(
    "generation",
    __name__,
)


def current_user_id() -> int:
    return int(
        g.current_user["id"]
    )


def current_user_roles() -> set[str]:
    return set(
        g.current_user.get("roles")
        or []
    )


def error_response(
    code: str,
    message: str,
    status: int,
):
    return (
        jsonify(
            {
                "success": False,

                "error": {
                    "code": code,
                    "message": message,
                },
            }
        ),
        status,
    )


def date_to_json(
    value,
) -> str | None:
    return (
        value.isoformat()
        if value
        else None
    )


def generation_to_json(
    generation: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id":
            generation["id"],

        "projectId":
            generation["project_id"],

        "analysisRunId":
            generation["analysis_run_id"],

        "environmentId":
            generation["environment_id"],

        "status":
            generation["status"],

        "progress":
            generation["progress"],

        "currentStep":
            generation["current_step"],

        "summary":
            generation["summary"]
            or {},

        "error": (
            {
                "code":
                    generation[
                        "error_code"
                    ],

                "message":
                    generation[
                        "error_message"
                    ],
            }

            if generation[
                "error_code"
            ]

            else None
        ),

        "project": {
            "name":
                generation.get(
                    "project_name"
                ),

            "slug":
                generation.get(
                    "project_slug"
                ),
        },

        "analysis": {
            "version":
                generation.get(
                    "analyzed_commit_sha"
                ),

            "confirmedAt":
                date_to_json(
                    generation.get(
                        "analysis_confirmed_at"
                    )
                ),
        },

        "environment": {
            "name":
                generation.get(
                    "environment_name"
                ),

            "code":
                generation.get(
                    "environment_code"
                ),

            "environmentType":
                generation.get(
                    "environment_type"
                ),

            "namespace":
                generation.get(
                    "environment_namespace"
                ),

            "domain":
                generation.get(
                    "environment_domain"
                ),
        },

        "createdBy":
            generation["created_by"],

        "createdAt":
            date_to_json(
                generation["created_at"]
            ),

        "startedAt":
            date_to_json(
                generation["started_at"]
            ),

        "finishedAt":
            date_to_json(
                generation["finished_at"]
            ),
    }


def event_to_json(
    event: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id":
            event["id"],

        "level":
            event["level"],

        "step":
            event["step"],

        "message":
            event["message"],

        "details":
            event["details"]
            or {},

        "createdAt":
            date_to_json(
                event["created_at"]
            ),
    }


def artifact_to_json(
    artifact: dict[str, Any],
    *,
    include_content: bool,
) -> dict[str, Any]:
    result = {
        "id":
            artifact["id"],

        "generationRunId":
            artifact[
                "generation_run_id"
            ],

        "projectId":
            artifact["project_id"],

        "componentId":
            artifact["component_id"],

        "componentName":
            artifact.get(
                "component_name"
            ),

        "componentRootPath":
            artifact.get(
                "component_root_path"
            ),

        "artifactType":
            artifact["artifact_type"],

        "relativePath":
            artifact["relative_path"],

        "contentSha256":
            artifact[
                "content_sha256"
            ],

        "artifactStatus":
            artifact[
                "artifact_status"
            ],

        "reviewStatus":
            artifact[
                "review_status"
            ],

        "metadata":
            artifact["metadata"]
            or {},

        "createdAt":
            date_to_json(
                artifact["created_at"]
            ),

        "updatedAt":
            date_to_json(
                artifact["updated_at"]
            ),
    }

    if include_content:
        result["content"] = (
            artifact["content"]
        )

        result["originalContent"] = (
            artifact[
                "original_content"
            ]
        )

    return result


@generation_blueprint.post(
    "/<int:project_id>/generations"
)
@require_auth
@require_project_access
def start_generation_route(
    project_id: int,
):
    try:
        generation = (
            start_project_generation(
                project_id=project_id,

                user_id=
                    current_user_id(),

                roles=
                    current_user_roles(),
            )
        )

    except GenerationServiceError as error:
        return error_response(
            error.code,
            error.message,
            error.http_status,
        )

    return (
        jsonify(
            {
                "success": True,

                "data": {
                    "generation":
                        generation_to_json(
                            generation
                        ),
                },
            }
        ),
        202,
    )


@generation_blueprint.get(
    "/<int:project_id>/generations/latest"
)
@require_auth
@require_project_access
def latest_generation_route(
    project_id: int,
):
    try:
        generation = (
            get_latest_project_generation(
                project_id
            )
        )

    except GenerationServiceError as error:
        return error_response(
            error.code,
            error.message,
            error.http_status,
        )

    return jsonify(
        {
            "success": True,

            "data": {
                "generation":
                    generation_to_json(
                        generation
                    ),
            },
        }
    )


@generation_blueprint.get(
    (
        "/<int:project_id>/generations/"
        "<int:generation_run_id>"
    )
)
@require_auth
@require_project_access
def generation_detail_route(
    project_id: int,
    generation_run_id: int,
):
    try:
        generation = (
            get_project_generation(
                project_id=project_id,

                generation_run_id=
                    generation_run_id,
            )
        )

    except GenerationServiceError as error:
        return error_response(
            error.code,
            error.message,
            error.http_status,
        )

    return jsonify(
        {
            "success": True,

            "data": {
                "generation":
                    generation_to_json(
                        generation
                    ),
            },
        }
    )


@generation_blueprint.get(
    (
        "/<int:project_id>/generations/"
        "<int:generation_run_id>/events"
    )
)
@require_auth
@require_project_access
def generation_events_route(
    project_id: int,
    generation_run_id: int,
):
    raw_after_id = request.args.get(
        "afterId",
        "0",
    )

    try:
        after_id = int(
            raw_after_id
        )

    except ValueError:
        return error_response(
            "INVALID_AFTER_ID",
            (
                "Le paramètre afterId "
                "est invalide."
            ),
            400,
        )

    if after_id < 0:
        return error_response(
            "INVALID_AFTER_ID",
            (
                "Le paramètre afterId "
                "est invalide."
            ),
            400,
        )

    try:
        events = (
            get_project_generation_events(
                project_id=project_id,

                generation_run_id=
                    generation_run_id,

                after_id=after_id,
            )
        )

    except GenerationServiceError as error:
        return error_response(
            error.code,
            error.message,
            error.http_status,
        )

    return jsonify(
        {
            "success": True,

            "data": {
                "events": [
                    event_to_json(event)

                    for event in events
                ],
            },
        }
    )


@generation_blueprint.get(
    (
        "/<int:project_id>/generations/"
        "<int:generation_run_id>/artifacts"
    )
)
@require_auth
@require_project_access
def generation_artifacts_route(
    project_id: int,
    generation_run_id: int,
):
    try:
        artifacts = (
            get_project_generation_artifacts(
                project_id=project_id,

                generation_run_id=
                    generation_run_id,
            )
        )

    except GenerationServiceError as error:
        return error_response(
            error.code,
            error.message,
            error.http_status,
        )

    return jsonify(
        {
            "success": True,

            "data": {
                "artifacts": [
                    artifact_to_json(
                        artifact,
                        include_content=False,
                    )

                    for artifact
                    in artifacts
                ],

                "total":
                    len(artifacts),
            },
        }
    )


@generation_blueprint.get(
    (
        "/<int:project_id>/generations/"
        "<int:generation_run_id>/artifacts/"
        "<int:artifact_id>"
    )
)
@require_auth
@require_project_access
def generation_artifact_detail_route(
    project_id: int,
    generation_run_id: int,
    artifact_id: int,
):
    try:
        artifact = (
            get_project_generation_artifact(
                project_id=project_id,

                generation_run_id=
                    generation_run_id,

                artifact_id=
                    artifact_id,
            )
        )

    except GenerationServiceError as error:
        return error_response(
            error.code,
            error.message,
            error.http_status,
        )

    return jsonify(
        {
            "success": True,

            "data": {
                "artifact":
                    artifact_to_json(
                        artifact,
                        include_content=True,
                    ),
            },
        }
    )