from __future__ import annotations

from typing import Any

from app.integrations.security import (
    decrypt_credential,
    encrypt_credential,
)

from app.projects.repository import (
    create_project,
    find_environments,
    find_git_connection,
    find_project,
    list_available_environments,
    list_git_connections,
    list_projects,
    save_source_check,
)

from app.projects.source_provider import (
    SourceProviderError,
    SourceValidationResult,
    git_source_provider,
)


class ProjectServiceError(RuntimeError):
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


def get_project_options() -> dict[str, Any]:
    return {
        "gitConnections":
            list_git_connections(),

        "environments":
            list_available_environments(),
    }


def get_projects(
    *,
    status: str | None,
    search: str | None,
) -> list[dict[str, Any]]:
    return list_projects(
        status=status,
        search=search,
    )


def get_project_by_id(
    project_id: int,
) -> dict[str, Any]:
    project = find_project(project_id)

    if project is None:
        raise ProjectServiceError(
            "PROJECT_NOT_FOUND",
            "Le projet est introuvable.",
            404,
        )

    return project


def resolve_credential(
    *,
    connection: dict[str, Any],
    data: dict[str, Any],
) -> dict[str, Any]:
    if data["visibility"] == "public":
        return {
            "credential_source": "none",
            "auth_method": "none",
            "token_type": None,
            "username": None,
            "secret": None,
        }

    if data["credential_source"] == "integration":
        if not connection["credential_configured"]:
            raise ProjectServiceError(
                "INTEGRATION_CREDENTIAL_NOT_CONFIGURED",
                (
                    "La connexion GitLab ne contient "
                    "aucun credential."
                ),
                400,
            )

        integration_auth_type = (
            connection["credential_auth_type"]
        )

        if integration_auth_type == "basic":
            auth_method = "https_password"

        elif integration_auth_type == "token":
            auth_method = "https_token"

        elif integration_auth_type == "ssh_key":
            auth_method = "ssh_key"

        else:
            raise ProjectServiceError(
                "INTEGRATION_CREDENTIAL_NOT_SUPPORTED",
                (
                    "Le credential de la connexion "
                    "n'est pas compatible avec Git."
                ),
                400,
            )

        if (
            data["transport"] == "https"
            and auth_method == "ssh_key"
        ):
            raise ProjectServiceError(
                "INTEGRATION_TRANSPORT_MISMATCH",
                (
                    "La connexion contient une clé SSH, "
                    "mais HTTPS a été sélectionné."
                ),
                400,
            )

        if (
            data["transport"] == "ssh"
            and auth_method != "ssh_key"
        ):
            raise ProjectServiceError(
                "INTEGRATION_TRANSPORT_MISMATCH",
                (
                    "La connexion ne contient pas "
                    "de clé privée SSH."
                ),
                400,
            )

        return {
            "credential_source": "integration",

            "auth_method":
                auth_method,

            "token_type": (
                "generic_token"
                if auth_method == "https_token"
                else None
            ),

            "username":
                connection[
                    "credential_username"
                ],

            "secret":
                decrypt_credential(
                    connection[
                        "secret_ciphertext"
                    ]
                ),
        }

    return {
        "credential_source": "project",
        "auth_method": data["auth_method"],
        "token_type": data["token_type"],
        "username": data["username"],
        "secret": data["secret"],
    }


def validate_source(
    *,
    user_id: int,
    data: dict[str, Any],
    save_check: bool = True,
) -> tuple[
    SourceValidationResult,
    dict[str, Any],
]:
    connection = find_git_connection(
        data["source_connection_id"]
    )

    if connection is None:
        raise ProjectServiceError(
            "GIT_CONNECTION_NOT_FOUND",
            (
                "La connexion GitLab est "
                "introuvable ou désactivée."
            ),
            404,
        )

    credential = resolve_credential(
        connection=connection,
        data=data,
    )

    try:
        validation = (
            git_source_provider
            .validate_repository(
                connection=connection,

                repository_url=
                    data["repository_url"],

                visibility=
                    data["visibility"],

                transport=
                    data["transport"],

                branch=
                    data["branch"],

                username=
                    credential["username"],

                secret=
                    credential["secret"],
            )
        )

    except SourceProviderError as error:
        if save_check:
            save_source_check(
                project_id=None,
                user_id=user_id,

                source_connection_id=
                    data["source_connection_id"],

                repository_path=
                    data["repository_url"],

                branch=data["branch"],

                status=(
                    "error"
                    if error.http_status >= 500
                    else "invalid"
                ),

                commit_sha=None,

                error_code=error.code,
                error_message=error.message,

                details={
                    "visibility":
                        data["visibility"],

                    "transport":
                        data["transport"],

                    "credentialSource":
                        credential[
                            "credential_source"
                        ],

                    "authMethod":
                        credential["auth_method"],
                },
            )

        raise ProjectServiceError(
            error.code,
            error.message,
            error.http_status,
        ) from error

    if save_check:
        save_source_check(
            project_id=None,
            user_id=user_id,

            source_connection_id=
                data["source_connection_id"],

            repository_path=
                validation.repository_path,

            branch=
                validation.branch,

            status="valid",

            commit_sha=
                validation.commit_sha,

            error_code=None,
            error_message=None,

            details={
                **validation.to_dict(),

                "credentialSource":
                    credential[
                        "credential_source"
                    ],

                "authMethod":
                    credential["auth_method"],
            },
        )

    return validation, credential


def create_new_project(
    *,
    user_id: int,
    roles: set[str],
    data: dict[str, Any],
) -> dict[str, Any]:
    allowed_roles = {
        "admin",
        "administrator",
        "devops",
        "developer",
    }

    if not roles.intersection(
        allowed_roles
    ):
        raise ProjectServiceError(
            "PROJECT_CREATE_FORBIDDEN",
            (
                "Votre rôle ne permet pas "
                "de créer un projet."
            ),
            403,
        )

    environment_ids = (
        data["allowed_environment_ids"]
    )

    environments = find_environments(
        environment_ids
    )

    found_ids = {
        int(environment["id"])
        for environment in environments
    }

    if found_ids != set(environment_ids):
        raise ProjectServiceError(
            "INVALID_ENVIRONMENTS",
            (
                "Un ou plusieurs environnements "
                "sont introuvables ou archivés."
            ),
            400,
        )

    if (
        data["default_environment_id"]
        not in found_ids
    ):
        raise ProjectServiceError(
            "INVALID_DEFAULT_ENVIRONMENT",
            (
                "L'environnement par défaut "
                "n'est pas autorisé."
            ),
            400,
        )

    validation, credential = validate_source(
        user_id=user_id,
        data=data,
        save_check=False,
    )

    encrypted_secret = None

    if (
        credential["credential_source"]
        == "project"
    ):
        encrypted_secret = encrypt_credential(
            credential["secret"]
        )

    project = create_project(
        name=data["name"],
        description=data["description"],

        source_connection_id=
            data["source_connection_id"],

        repository_url=
            validation.repository_url,

        repository_path=
            validation.repository_path,

        repository_visibility=
            validation.visibility,

        source_transport=
            validation.transport,

        credential_source=
            credential[
                "credential_source"
            ],

        auth_method=
            credential["auth_method"],

        token_type=
            credential["token_type"],

        username=
            credential["username"],

        encrypted_secret=
            encrypted_secret,

        branch=
            validation.branch,

        source_subdirectory=
            data["source_subdirectory"],

        commit_sha=
            validation.commit_sha,

        environment_ids=
            environment_ids,

        default_environment_id=
            data["default_environment_id"],

        user_id=user_id,
    )

    save_source_check(
        project_id=int(project["id"]),
        user_id=user_id,

        source_connection_id=
            data["source_connection_id"],

        repository_path=
            validation.repository_path,

        branch=validation.branch,
        status="valid",
        commit_sha=validation.commit_sha,

        error_code=None,
        error_message=None,

        details={
            **validation.to_dict(),

            "credentialSource":
                credential[
                    "credential_source"
                ],

            "authMethod":
                credential["auth_method"],
        },
    )

    return {
        "project": project,
        "sourceValidation":
            validation.to_dict(),
    }