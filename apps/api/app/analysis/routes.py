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

from app.analysis.service import (
    AnalysisServiceError,
    confirm_project_analysis,
    get_latest_project_analysis,
    get_project_analysis,
    get_project_analysis_events,
    start_project_analysis,
    update_analysis_component,
)

from app.analysis.validators import (
    AnalysisValidationError,
    read_component_update_payload,
    read_start_analysis_payload,
)


analysis_blueprint = Blueprint(
    "analysis",
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


def component_to_json(
    component: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id":
            component["id"],

        "name":
            component["name"],

        "componentType":
            component[
                "component_type"
            ],

        "rootPath":
            component["root_path"],

        "runtime":
            component["runtime"],

        "framework":
            component["framework"],

        "packageManager":
            component[
                "package_manager"
            ],

        "buildCommand":
            component[
                "build_command"
            ],

        "startCommand":
            component[
                "start_command"
            ],

        "detectedPort":
            component[
                "detected_port"
            ],

        "deployable":
            component["deployable"],

        "dockerfilePath":
            component[
                "dockerfile_path"
            ],

        "helmChartPath":
            component[
                "helm_chart_path"
            ],

        "kubernetesPaths":
            component[
                "kubernetes_paths"
            ]
            or [],

        "environmentVariables":
            component[
                "environment_variables"
            ]
            or [],

        "confidence":
            component["confidence"],

        "configuration":
            component["configuration"]
            or {},

        "userModified":
            component[
                "user_modified"
            ],
    }


def analysis_to_json(
    analysis: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id":
            analysis["id"],

        "projectId":
            analysis["project_id"],

        "commitPolicy":
            analysis[
                "commit_policy"
            ],

        "requestedCommitSha":
            analysis[
                "requested_commit_sha"
            ],

        "branchHeadSha":
            analysis[
                "branch_head_sha"
            ],

        "analyzedCommitSha":
            analysis[
                "analyzed_commit_sha"
            ],

        "selectedSubdirectory":
            analysis[
                "selected_subdirectory"
            ],

        "status":
            analysis["status"],

        "progress":
            analysis["progress"],

        "currentStep":
            analysis[
                "current_step"
            ],

        "summary":
            analysis["summary"]
            or {},

        "error": (
            {
                "code":
                    analysis[
                        "error_code"
                    ],

                "message":
                    analysis[
                        "error_message"
                    ],
            }

            if analysis[
                "error_code"
            ]

            else None
        ),

        "components": [
            component_to_json(
                component
            )

            for component
            in analysis.get(
                "components",
                [],
            )
        ],

        "createdAt":
            date_to_json(
                analysis["created_at"]
            ),

        "startedAt":
            date_to_json(
                analysis["started_at"]
            ),

        "finishedAt":
            date_to_json(
                analysis["finished_at"]
            ),

        "confirmedAt":
            date_to_json(
                analysis["confirmed_at"]
            ),
    }


@analysis_blueprint.post(
    "/<int:project_id>/analyses"
)
@require_auth
@require_project_access
def start_analysis_route(
    project_id: int,
):
    payload = request.get_json(
        silent=True
    )

    try:
        data = read_start_analysis_payload(
            payload
            if isinstance(payload, dict)
            else None
        )

        analysis = start_project_analysis(
            project_id=project_id,

            user_id=
                current_user_id(),

            roles=
                current_user_roles(),

            commit_policy=
                data["commit_policy"],
        )

    except AnalysisValidationError as error:
        return error_response(
            error.code,
            error.message,
            400,
        )

    except AnalysisServiceError as error:
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
                    "analysis":
                        analysis_to_json(
                            {
                                **analysis,
                                "components": [],
                            }
                        ),
                },
            }
        ),
        202,
    )


@analysis_blueprint.get(
    "/<int:project_id>/analyses/latest"
)
@require_auth
@require_project_access
def latest_analysis_route(
    project_id: int,
):
    try:
        analysis = (
            get_latest_project_analysis(
                project_id
            )
        )

    except AnalysisServiceError as error:
        return error_response(
            error.code,
            error.message,
            error.http_status,
        )

    return jsonify(
        {
            "success": True,

            "data": {
                "analysis":
                    analysis_to_json(
                        analysis
                    ),
            },
        }
    )


@analysis_blueprint.get(
    (
        "/<int:project_id>/analyses/"
        "<int:analysis_run_id>"
    )
)
@require_auth
@require_project_access
def analysis_detail_route(
    project_id: int,
    analysis_run_id: int,
):
    try:
        analysis = get_project_analysis(
            project_id=project_id,

            analysis_run_id=
                analysis_run_id,
        )

    except AnalysisServiceError as error:
        return error_response(
            error.code,
            error.message,
            error.http_status,
        )

    return jsonify(
        {
            "success": True,

            "data": {
                "analysis":
                    analysis_to_json(
                        analysis
                    ),
            },
        }
    )


@analysis_blueprint.get(
    (
        "/<int:project_id>/analyses/"
        "<int:analysis_run_id>/events"
    )
)
@require_auth
@require_project_access
def analysis_events_route(
    project_id: int,
    analysis_run_id: int,
):
    raw_after_id = request.args.get(
        "afterId",
        "0",
    )

    try:
        after_id = max(
            int(raw_after_id),
            0,
        )

    except ValueError:
        return error_response(
            "INVALID_AFTER_ID",
            (
                "L'identifiant de pagination "
                "est invalide."
            ),
            400,
        )

    try:
        events = (
            get_project_analysis_events(
                project_id=project_id,

                analysis_run_id=
                    analysis_run_id,

                after_id=
                    after_id,
            )
        )

    except AnalysisServiceError as error:
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
                    {
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
                                event[
                                    "created_at"
                                ]
                            ),
                    }

                    for event in events
                ],
            },
        }
    )


@analysis_blueprint.patch(
    (
        "/<int:project_id>/analyses/"
        "<int:analysis_run_id>/components/"
        "<int:component_id>"
    )
)
@require_auth
@require_project_access
def update_component_route(
    project_id: int,
    analysis_run_id: int,
    component_id: int,
):
    payload = request.get_json(
        silent=True
    )

    try:
        changes = (
            read_component_update_payload(
                payload
                if isinstance(
                    payload,
                    dict,
                )
                else None
            )
        )

        component = (
            update_analysis_component(
                project_id=project_id,

                analysis_run_id=
                    analysis_run_id,

                component_id=
                    component_id,

                roles=
                    current_user_roles(),

                changes=
                    changes,
            )
        )

    except AnalysisValidationError as error:
        return error_response(
            error.code,
            error.message,
            400,
        )

    except AnalysisServiceError as error:
        return error_response(
            error.code,
            error.message,
            error.http_status,
        )

    return jsonify(
        {
            "success": True,

            "data": {
                "component":
                    component_to_json(
                        component
                    ),
            },
        }
    )


@analysis_blueprint.post(
    (
        "/<int:project_id>/analyses/"
        "<int:analysis_run_id>/confirm"
    )
)
@require_auth
@require_project_access
def confirm_analysis_route(
    project_id: int,
    analysis_run_id: int,
):
    try:
        confirm_project_analysis(
            project_id=project_id,

            analysis_run_id=
                analysis_run_id,

            user_id=
                current_user_id(),

            roles=
                current_user_roles(),
        )

    except AnalysisServiceError as error:
        return error_response(
            error.code,
            error.message,
            error.http_status,
        )

    return jsonify(
        {
            "success": True,

            "data": {
                "confirmed": True,
            },
        }
    )