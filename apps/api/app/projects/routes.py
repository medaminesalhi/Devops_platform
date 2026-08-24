from __future__ import annotations

import json

from typing import Any

from flask import (
    Blueprint,
    current_app,
    g,
    jsonify,
    request,
)

from werkzeug.datastructures import (
    FileStorage,
)

from app.auth.decorators import (
    current_user_is_admin,
    require_auth,
    require_project_access,
)

from app.projects.service import (
    ProjectServiceError,
    create_new_project,
    delete_project_by_id,
    get_project_by_id,
    get_project_options,
    get_projects,
    validate_git_source,
    validate_zip_source,
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


def read_request_payload() -> tuple[
    dict[str, Any],
    FileStorage | None,
]:
    if request.is_json:
        payload = request.get_json(
            silent=True
        )

        if not isinstance(payload, dict):
            raise ProjectValidationError(
                "INVALID_JSON",
                "Le corps JSON est invalide.",
            )

        return payload, None

    payload_text = request.form.get(
        "payload",
        "",
    ).strip()

    if not payload_text:
        raise ProjectValidationError(
            "INVALID_MULTIPART_PAYLOAD",
            (
                "Le formulaire multipart ne contient "
                "pas le champ payload."
            ),
        )

    try:
        payload = json.loads(
            payload_text
        )

    except json.JSONDecodeError as error:
        raise ProjectValidationError(
            "INVALID_JSON",
            (
                "Le champ payload contient "
                "un JSON invalide."
            ),
        ) from error

    if not isinstance(payload, dict):
        raise ProjectValidationError(
            "INVALID_JSON",
            (
                "Le champ payload doit contenir "
                "un objet JSON."
            ),
        )

    return (
        payload,
        request.files.get("archiveFile"),
    )


def git_validation_json(
    validation: dict[str, Any],
) -> dict[str, Any]:
    return {
        "sourceType": "git",

        "repositoryUrl":
            validation.get(
                "repository_url"
            ),

        "repositoryPath":
            validation.get(
                "repository_path"
            ),

        "repositoryHost":
            validation.get(
                "repository_host"
            ),

        "branch":
            validation.get("branch"),

        "commitSha":
            validation.get("commit_sha"),

        "visibility":
            validation.get("visibility"),

        "transport":
            validation.get(
                "transport",
                "https",
            ),

        "archive": None,

        "validationMethod":
            validation.get(
                "validation_method",
                "git_ls_remote",
            ),
    }


def zip_validation_json(
    validation: dict[str, Any],
) -> dict[str, Any]:
    return {
        "sourceType": "zip",

        "repositoryUrl": None,
        "repositoryPath": None,
        "repositoryHost": None,

        "branch": None,
        "commitSha": None,

        "visibility": None,
        "transport": "archive",

        "archive": {
            "originalName":
                validation.get(
                    "original_name"
                ),

            "sizeBytes":
                validation.get(
                    "size_bytes"
                ),

            "sha256":
                validation.get(
                    "sha256"
                ),

            "entryCount":
                validation.get(
                    "entry_count"
                ),

            "uncompressedBytes":
                validation.get(
                    "uncompressed_bytes"
                ),

            "topLevelEntries":
                validation.get(
                    "top_level_entries"
                )
                or [],
        },

        "validationMethod":
            validation.get(
                "validation_method",
                "zip_inspection",
            ),
    }


def source_validation_json(
    validation: dict[str, Any],
) -> dict[str, Any]:
    source_type = validation.get(
        "source_type"
    )

    if source_type is None:
        source_type = (
            "zip"
            if "original_name" in validation
            else "git"
        )

    if source_type == "zip":
        return zip_validation_json(
            validation
        )

    return git_validation_json(
        validation
    )


def project_json(
    project: dict[str, Any],
) -> dict[str, Any]:
    source_type = (
        project.get("source_type")
        or "git"
    )

    is_zip = source_type == "zip"

    if is_zip:
        repository_path = project.get(
            "archive_original_name"
        )

        visibility = "private"
        transport = "archive"
        branch = "Archive ZIP"

        credential_source = "none"
        auth_method = "none"
        token_type = None
        username = None

        credential_configured = True

    else:
        repository_path = project.get(
            "repository_path"
        )

        visibility = (
            project.get(
                "repository_visibility"
            )
            or "private"
        )

        transport = (
            project.get(
                "source_transport"
            )
            or "https"
        )

        branch = (
            project.get(
                "default_branch"
            )
            or "main"
        )

        credential_source = (
            project.get(
                "source_credential_source"
            )
            or "none"
        )

        auth_method = (
            project.get(
                "source_auth_method"
            )
            or "none"
        )

        token_type = project.get(
            "source_token_type"
        )

        username = project.get(
            "source_username"
        )

        credential_configured = bool(
            project.get(
                "source_credential_configured"
            )
        )

    last_checked_at = project.get(
        "last_source_check_at"
    )

    created_at = project.get(
        "created_at"
    )

    updated_at = project.get(
        "updated_at"
    )

    default_environment_id = (
        project.get(
            "default_environment_id"
        )
    )

    return {
        "id":
            project["id"],

        "name":
            project["name"],

        "slug":
            project["slug"],

        "description":
            project.get(
                "description"
            ),

        "operationMode":
            project.get(
                "operation_mode"
            )
            or "new_application",

        "status":
            project["status"],

        "source": {
            "type":
                source_type,

            "provider":
                project.get("source_provider")
                or ("archive" if is_zip else "gitlab"),

            "connectionId":
                project.get(
                    "source_connection_id"
                ),

            "connectionName":
                project.get(
                    "source_connection_name"
                ),

            "baseUrl":
                project.get(
                    "source_base_url"
                ),

            "repositoryUrl":
                project.get(
                    "repository_url"
                ),

            "repositoryPath":
                repository_path,

            "visibility":
                visibility,

            "transport":
                transport,

            "credentialSource":
                credential_source,

            "authMethod":
                auth_method,

            "tokenType":
                token_type,

            "username":
                username,

            "credentialConfigured":
                credential_configured,

            "branch":
                branch,

            "subdirectory":
                project.get(
                    "source_subdirectory"
                ),

            "archive": (
                {
                    "originalName":
                        project.get(
                            "archive_original_name"
                        ),

                    "sizeBytes":
                        project.get(
                            "archive_size_bytes"
                        ),

                    "sha256":
                        project.get(
                            "archive_sha256"
                        ),

                    "entryCount":
                        project.get(
                            "archive_entry_count"
                        ),

                    "uncompressedBytes":
                        project.get(
                            "archive_uncompressed_bytes"
                        ),
                }

                if is_zip
                else None
            ),

            "status":
                project.get(
                    "source_status"
                ),

            "error":
                project.get(
                    "source_error"
                ),

            "lastCommitSha":
                project.get(
                    "last_source_commit_sha"
                ),

            "lastCheckedAt": (
                last_checked_at.isoformat()

                if last_checked_at
                else None
            ),
        },

        "defaultEnvironment": (
            {
                "id":
                    default_environment_id,

                "name":
                    project.get(
                        "default_environment_name"
                    ),

                "environmentType":
                    project.get(
                        "default_environment_type"
                    ),

                "namespace":
                    project.get(
                        "default_environment_namespace"
                    ),
            }

            if default_environment_id
            else None
        ),

        "environments":
            project.get(
                "environments"
            )
            or [],

        "createdBy":
            project.get(
                "created_by"
            ),

        "createdAt": (
            created_at.isoformat()
            if created_at
            else None
        ),

        "updatedAt": (
            updated_at.isoformat()
            if updated_at
            else None
        ),
    }


@projects_blueprint.get(
    "/options"
)
@require_auth
def options_route():
    options = get_project_options(
        owner_user_id=(
            None
            if current_user_is_admin()
            else current_user_id()
        )
    )

    maximum_bytes = int(
        current_app.config.get(
            "PROJECT_ARCHIVE_MAX_BYTES",
            100 * 1024 * 1024,
        )
    )

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

                        "providerType":
                            connection["provider_type"],

                        "baseUrl":
                            connection["base_url"],

                        "status":
                            connection["status"],

                        "verifySsl":
                            connection["verify_ssl"],

                        "sshHost":
                            connection["ssh_host"],

                        "sshPort":
                            connection["ssh_port"],

                        "sshUsername":
                            connection["ssh_username"],

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
                    in options[
                        "environments"
                    ]
                ],

                "archiveLimits": {
                    "maxBytes":
                        maximum_bytes,

                    "maxMegabytes":
                        round(
                            maximum_bytes
                            / 1024
                            / 1024
                        ),

                    "maxEntries":
                        int(
                            current_app.config.get(
                                "PROJECT_ARCHIVE_MAX_ENTRIES",
                                20_000,
                            )
                        ),
                },
            },
        }
    )


@projects_blueprint.post(
    "/validate-source"
)
@require_auth
def validate_source_route():
    try:
        payload, archive_file = (
            read_request_payload()
        )

        data = read_source_payload(
            payload
        )

        if data["source_type"] == "zip":
            validation = (
                validate_zip_source(
                    user_id=
                        current_user_id(),

                    archive_file=
                        archive_file,
                )
            )

        else:
            validation, _ = (
                validate_git_source(
                    user_id=
                        current_user_id(),

                    data=data,
                    owner_user_id=(
                        None
                        if current_user_is_admin()
                        else current_user_id()
                    ),
                )
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
            (
                "Erreur pendant la validation "
                "de la source."
            )
        )

        return error_response(
            "SOURCE_VALIDATION_FAILED",
            (
                "La validation de la source "
                "a échoué."
            ),
            500,
        )

    return jsonify(
        {
            "success": True,

            "data": {
                "sourceValidation":
                    source_validation_json(
                        validation.to_dict()
                    ),
            },
        }
    )


@projects_blueprint.post("")
@require_auth
def create_project_route():
    try:
        payload, archive_file = (
            read_request_payload()
        )

        data = (
            read_create_project_payload(
                payload
            )
        )

        result = create_new_project(
            user_id=current_user_id(),
            roles=current_user_roles(),
            data=data,
            archive_file=archive_file,
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
            (
                "Erreur pendant la création "
                "du projet."
            )
        )

        return error_response(
            "PROJECT_CREATE_FAILED",
            (
                "La création du projet "
                "a échoué."
            ),
            500,
        )

    return (
        jsonify(
            {
                "success": True,

                "data": {
                    "project":
                        project_json(
                            result[
                                "project"
                            ]
                        ),

                    "sourceValidation":
                        source_validation_json(
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
    status = request.args.get(
        "status"
    )

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
        owner_user_id=(
            None
            if current_user_is_admin()
            else current_user_id()
        ),
    )

    return jsonify(
        {
            "success": True,

            "data": {
                "projects": [
                    project_json(project)

                    for project
                    in projects
                ],

                "total":
                    len(projects),
            },
        }
    )


@projects_blueprint.get(
    "/<int:project_id>"
)
@require_auth
@require_project_access
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
                    project_json(
                        project
                    ),
            },
        }
    )


@projects_blueprint.delete(
    "/<int:project_id>"
)
@require_auth
def delete_project_route(
    project_id: int,
):
    if not current_user_is_admin():
        return error_response(
            "PROJECT_DELETE_FORBIDDEN",
            "Seul un administrateur peut supprimer un projet.",
            403,
        )

    try:
        deleted = delete_project_by_id(project_id)
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
                "deletedProject": {
                    "id": int(deleted["id"]),
                    "name": deleted["name"],
                },
            },
        }
    )


# Routes additionnelles du parcours progressif.
from app.projects import drafts as _draft_routes  # noqa: E402,F401
from app.projects import proposals as _proposal_routes  # noqa: E402,F401