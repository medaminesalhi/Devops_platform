from __future__ import annotations

from typing import Any

from flask import (
    Blueprint,
    current_app,
    g,
    jsonify,
    request,
)

from app.auth.decorators import (
    require_auth,
)

from app.projects.service import (
    ProjectServiceError,
    create_new_project,
    get_project_by_id,
    get_project_options,
    get_projects,
    validate_source,
)

from app.projects.validators import (
    ProjectValidationError,
    read_create_project_payload,
    read_source_payload,
)


projects_blueprint = Blueprint(
    "projects",
    __name__,
)


PROJECT_STATUSES = {
    "draft",
    "active",
    "source_error",
    "archived",
}


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


def validation_json(
    validation: dict[str, Any],
) -> dict[str, Any]:
    return {
        "repositoryUrl":
            validation["repository_url"],

        "repositoryPath":
            validation["repository_path"],

        "repositoryHost":
            validation["repository_host"],

        "branch":
            validation["branch"],

        "commitSha":
            validation["commit_sha"],

        "visibility":
            validation["visibility"],

        "transport":
            validation["transport"],

        "validationMethod":
            validation["validation_method"],
    }


def project_json(
    project: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": project["id"],
        "name": project["name"],
        "slug": project["slug"],
        "description": project["description"],
        "status": project["status"],

        "source": {
            "connectionId":
                project[
                    "source_connection_id"
                ],

            "connectionName":
                project[
                    "source_connection_name"
                ],

            "baseUrl":
                project[
                    "source_base_url"
                ],

            "repositoryUrl":
                project["repository_url"],

            "repositoryPath":
                project["repository_path"],

            "visibility":
                project[
                    "repository_visibility"
                ],

            "transport":
                project["source_transport"],

            "credentialSource":
                project[
                    "source_credential_source"
                ],

            "authMethod":
                project[
                    "source_auth_method"
                ],

            "tokenType":
                project[
                    "source_token_type"
                ],

            "username":
                project["source_username"],

            "credentialConfigured":
                project[
                    "source_credential_configured"
                ],

            "branch":
                project["default_branch"],

            "subdirectory":
                project[
                    "source_subdirectory"
                ],

            "status":
                project["source_status"],

            "error":
                project["source_error"],

            "lastCommitSha":
                project[
                    "last_source_commit_sha"
                ],

            "lastCheckedAt": (
                project[
                    "last_source_check_at"
                ].isoformat()

                if project[
                    "last_source_check_at"
                ]
                else None
            ),
        },

        "defaultEnvironment": (
            {
                "id":
                    project[
                        "default_environment_id"
                    ],

                "name":
                    project[
                        "default_environment_name"
                    ],

                "environmentType":
                    project[
                        "default_environment_type"
                    ],

                "namespace":
                    project[
                        "default_environment_namespace"
                    ],
            }

            if project[
                "default_environment_id"
            ]
            else None
        ),

        "environments":
            project["environments"] or [],

        "createdBy":
            project["created_by"],

        "createdAt": (
            project["created_at"].isoformat()
            if project["created_at"]
            else None
        ),

        "updatedAt": (
            project["updated_at"].isoformat()
            if project["updated_at"]
            else None
        ),
    }


@projects_blueprint.get("/options")
@require_auth
def options_route():
    options = get_project_options()

    return jsonify(
        {
            "success": True,

            "data": {
                "gitConnections": [
                    {
                        "id":
                            connection["id"],

                        "name":
                            connection["name"],

                        "baseUrl":
                            connection[
                                "base_url"
                            ],

                        "status":
                            connection["status"],

                        "verifySsl":
                            connection[
                                "verify_ssl"
                            ],

                        "sshHost":
                            connection[
                                "ssh_host"
                            ],

                        "sshPort":
                            connection[
                                "ssh_port"
                            ],

                        "sshUsername":
                            connection[
                                "ssh_username"
                            ],

                        "credentialConfigured":
                            connection[
                                "credential_configured"
                            ],

                        "credentialAuthType":
                            connection[
                                "credential_auth_type"
                            ],

                        "credentialUsername":
                            connection[
                                "credential_username"
                            ],
                    }

                    for connection
                    in options[
                        "gitConnections"
                    ]
                ],

                "environments": [
                    {
                        "id":
                            environment["id"],

                        "name":
                            environment["name"],

                        "environmentType":
                            environment[
                                "environment_type"
                            ],

                        "namespace":
                            environment[
                                "namespace"
                            ],

                        "domain":
                            environment["domain"],

                        "configurationStatus":
                            environment[
                                "configuration_status"
                            ],
                    }

                    for environment
                    in options["environments"]
                ],
            },
        }
    )


@projects_blueprint.post(
    "/validate-source"
)
@require_auth
def validate_source_route():
    payload = request.get_json(
        silent=True
    )

    if not isinstance(payload, dict):
        return error_response(
            "INVALID_JSON",
            "Le corps JSON est invalide.",
            400,
        )

    try:
        data = read_source_payload(payload)

        validation, _ = validate_source(
            user_id=current_user_id(),
            data=data,
        )

    except ProjectValidationError as error:
        return error_response(
            error.code,
            error.message,
            400,
        )

    except ProjectServiceError as error:
        return error_response(
            error.code,
            error.message,
            error.http_status,
        )

    except Exception:
        current_app.logger.exception(
            "Erreur pendant le test Git."
        )

        return error_response(
            "SOURCE_VALIDATION_FAILED",
            "Le test du repository a échoué.",
            500,
        )

    return jsonify(
        {
            "success": True,

            "data": {
                "sourceValidation":
                    validation_json(
                        validation.to_dict()
                    ),
            },
        }
    )


@projects_blueprint.post("")
@require_auth
def create_project_route():
    payload = request.get_json(
        silent=True
    )

    if not isinstance(payload, dict):
        return error_response(
            "INVALID_JSON",
            "Le corps JSON est invalide.",
            400,
        )

    try:
        data = read_create_project_payload(
            payload
        )

        result = create_new_project(
            user_id=current_user_id(),
            roles=current_user_roles(),
            data=data,
        )

    except ProjectValidationError as error:
        return error_response(
            error.code,
            error.message,
            400,
        )

    except ProjectServiceError as error:
        return error_response(
            error.code,
            error.message,
            error.http_status,
        )

    except Exception:
        current_app.logger.exception(
            "Erreur pendant la création du projet."
        )

        return error_response(
            "PROJECT_CREATE_FAILED",
            "La création du projet a échoué.",
            500,
        )

    return (
        jsonify(
            {
                "success": True,

                "data": {
                    "project":
                        project_json(
                            result["project"]
                        ),

                    "sourceValidation":
                        validation_json(
                            result[
                                "sourceValidation"
                            ]
                        ),
                },
            }
        ),
        201,
    )


@projects_blueprint.get("")
@require_auth
def list_projects_route():
    status = request.args.get("status")

    if (
        status
        and status not in PROJECT_STATUSES
    ):
        return error_response(
            "INVALID_PROJECT_STATUS",
            "Le statut est invalide.",
            400,
        )

    search = (
        request.args.get("search")
        or ""
    ).strip() or None

    projects = get_projects(
        status=status,
        search=search,
    )

    return jsonify(
        {
            "success": True,

            "data": {
                "projects": [
                    project_json(project)
                    for project in projects
                ],

                "total": len(projects),
            },
        }
    )


@projects_blueprint.get(
    "/<int:project_id>"
)
@require_auth
def project_detail_route(
    project_id: int,
):
    try:
        project = get_project_by_id(
            project_id
        )

    except ProjectServiceError as error:
        return error_response(
            error.code,
            error.message,
            error.http_status,
        )

    return jsonify(
        {
            "success": True,

            "data": {
                "project":
                    project_json(project),
            },
        }
    )