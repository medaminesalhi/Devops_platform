from __future__ import annotations

from typing import Any

from werkzeug.datastructures import FileStorage

from app.integrations.security import (
    decrypt_credential,
    encrypt_credential,
)

from app.projects.archive_provider import (
    ArchiveProviderError,
    ArchiveValidationResult,
    archive_source_provider,
)

from app.projects.repository import (
    create_git_project,
    create_zip_project,
    delete_project,
    find_environment,
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


PROJECT_CREATE_ROLES = {
    "admin",
    "administrator",
    "devops",
    "developer",
}


def get_project_options(
    *,
    owner_user_id: int | None,
) -> dict[str, Any]:
    return {
        "gitConnections": list_git_connections(
            owner_user_id=owner_user_id,
        ),
        "environments": list_available_environments(
            owner_user_id=owner_user_id,
        ),
    }


def get_projects(
    *,
    status: str | None,
    search: str | None,
    owner_user_id: int | None = None,
) -> list[dict[str, Any]]:
    return list_projects(
        status=status,
        search=search,
        owner_user_id=owner_user_id,
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




def delete_project_by_id(
    project_id: int,
) -> dict[str, Any]:
    deleted = delete_project(project_id)

    if deleted is None:
        raise ProjectServiceError(
            "PROJECT_NOT_FOUND",
            "Le projet est introuvable.",
            404,
        )

    return deleted


def ensure_project_create_role(
    roles: set[str],
) -> None:
    if not roles.intersection(
        PROJECT_CREATE_ROLES
    ):
        raise ProjectServiceError(
            "PROJECT_CREATE_FORBIDDEN",
            (
                "Votre rôle ne permet pas "
                "de créer un projet."
            ),
            403,
        )


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
                    "La connexion Git ne contient "
                    "aucun credential."
                ),
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
            )

        username = connection.get("credential_username")
        if not username and auth_method == "https_token":
            username = (
                "x-access-token"
                if connection.get("provider_type") == "github"
                else "oauth2"
            )

        return {
            "credential_source": "integration",
            "auth_method": auth_method,

            "token_type": (
                "generic_token"
                if auth_method == "https_token"
                else None
            ),

            "username": username,

            "secret": decrypt_credential(
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


def validate_git_source(
    *,
    user_id: int,
    data: dict[str, Any],
    owner_user_id: int | None,
    save_check: bool = True,
) -> tuple[
    SourceValidationResult,
    dict[str, Any],
]:
    connection = find_git_connection(
        data["source_connection_id"],
        owner_user_id=owner_user_id,
    )

    if connection is None:
        raise ProjectServiceError(
            "GIT_CONNECTION_NOT_FOUND",
            (
                "La connexion Git est "
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
                repository_url=(
                    data["repository_url"]
                ),
                visibility=data["visibility"],
                transport=data["transport"],
                branch=data["branch"],
                username=credential["username"],
                secret=credential["secret"],
            )
        )

    except SourceProviderError as error:
        if save_check:
            save_source_check(
                project_id=None,
                user_id=user_id,

                source_connection_id=(
                    data["source_connection_id"]
                ),

                repository_path=(
                    data["repository_url"]
                ),

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
                    "sourceType": "git",
                    "visibility": data["visibility"],
                    "transport": data["transport"],

                    "credentialSource": credential[
                        "credential_source"
                    ],

                    "authMethod": credential[
                        "auth_method"
                    ],
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

            source_connection_id=(
                data["source_connection_id"]
            ),

            repository_path=(
                validation.repository_path
            ),

            branch=validation.branch,
            status="valid",
            commit_sha=validation.commit_sha,

            error_code=None,
            error_message=None,

            details={
                **validation.to_dict(),

                "credentialSource": credential[
                    "credential_source"
                ],

                "authMethod": credential[
                    "auth_method"
                ],
            },
        )

    return validation, credential


def validate_zip_source(
    *,
    user_id: int,
    archive_file: FileStorage | None,
    save_check: bool = True,
) -> ArchiveValidationResult:
    try:
        validation = (
            archive_source_provider
            .validate_upload(archive_file)
        )

    except ArchiveProviderError as error:
        if save_check:
            save_source_check(
                project_id=None,
                user_id=user_id,
                source_connection_id=None,

                repository_path=(
                    archive_file.filename
                    if archive_file
                    and archive_file.filename
                    else "archive.zip"
                ),

                branch="archive",

                status=(
                    "error"
                    if error.http_status >= 500
                    else "invalid"
                ),

                commit_sha=None,

                error_code=error.code,
                error_message=error.message,

                details={
                    "sourceType": "zip",
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
            source_connection_id=None,

            repository_path=(
                validation.original_name
            ),

            branch="archive",
            status="valid",
            commit_sha=None,

            error_code=None,
            error_message=None,

            details=validation.to_dict(),
        )

    return validation


def create_new_project(
    *,
    user_id: int,
    roles: set[str],
    data: dict[str, Any],
    archive_file: FileStorage | None = None,
) -> dict[str, Any]:
    ensure_project_create_role(roles)

    owner_user_id = (
        None
        if roles.intersection({"admin", "administrator"})
        else user_id
    )

    environment = find_environment(
        data["environment_id"],
        owner_user_id=owner_user_id,
    )

    if environment is None:
        raise ProjectServiceError(
            "INVALID_ENVIRONMENT",
            (
                "L'environnement sélectionné est "
                "introuvable ou archivé."
            ),
        )

    if data["source_type"] == "zip":
        return create_zip_source_project(
            user_id=user_id,
            data=data,
            archive_file=archive_file,
        )

    return create_git_source_project(
        user_id=user_id,
        data=data,
        owner_user_id=owner_user_id,
    )


def create_git_source_project(
    *,
    user_id: int,
    data: dict[str, Any],
    owner_user_id: int | None,
) -> dict[str, Any]:
    validation, credential = validate_git_source(
        user_id=user_id,
        data=data,
        owner_user_id=owner_user_id,
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

    connection = find_git_connection(
        data["source_connection_id"],
        owner_user_id=owner_user_id,
    )
    if connection is None:
        raise ProjectServiceError(
            "GIT_CONNECTION_NOT_FOUND",
            "La connexion Git est introuvable ou désactivée.",
            404,
        )

    project = create_git_project(
        name=data["name"],
        description=data["description"],

        operation_mode=data[
            "operation_mode"
        ],

        source_connection_id=(
            data["source_connection_id"]
        ),

        source_provider=str(
            connection.get("provider_type")
            or "gitlab"
        ),

        repository_url=(
            validation.repository_url
        ),

        repository_path=(
            validation.repository_path
        ),

        repository_visibility=(
            validation.visibility
        ),

        source_transport=(
            validation.transport
        ),

        credential_source=credential[
            "credential_source"
        ],

        auth_method=credential[
            "auth_method"
        ],

        token_type=credential[
            "token_type"
        ],

        username=credential["username"],

        encrypted_secret=encrypted_secret,

        branch=validation.branch,

        source_subdirectory=(
            data["source_subdirectory"]
        ),

        commit_sha=validation.commit_sha,

        environment_id=data[
            "environment_id"
        ],

        user_id=user_id,
    )

    save_source_check(
        project_id=int(project["id"]),
        user_id=user_id,

        source_connection_id=(
            data["source_connection_id"]
        ),

        repository_path=(
            validation.repository_path
        ),

        branch=validation.branch,
        status="valid",
        commit_sha=validation.commit_sha,

        error_code=None,
        error_message=None,

        details={
            **validation.to_dict(),

            "operationMode": data[
                "operation_mode"
            ],

            "credentialSource": credential[
                "credential_source"
            ],

            "authMethod": credential[
                "auth_method"
            ],

            "environmentId": data[
                "environment_id"
            ],
        },
    )

    return {
        "project": project,

        "sourceValidation":
            validation.to_dict(),
    }


def create_zip_source_project(
    *,
    user_id: int,
    data: dict[str, Any],
    archive_file: FileStorage | None,
) -> dict[str, Any]:
    stored_archive:ArchiveValidationResult | None = None

    try:
        stored_archive = (
            archive_source_provider
            .store_upload(archive_file)
        )

        if (
            not stored_archive.stored_name
            or not stored_archive.storage_path
        ):
            raise ProjectServiceError(
                "ARCHIVE_STORAGE_FAILED",
                "L'archive n'a pas pu être enregistrée.",
                500,
            )

        project = create_zip_project(
            name=data["name"],

            description=data["description"],

            operation_mode=data[
                "operation_mode"
            ],

            source_subdirectory=(
                data["source_subdirectory"]
            ),

            archive_original_name=(
                stored_archive.original_name
            ),

            archive_stored_name=(
                stored_archive.stored_name
            ),

            archive_storage_path=(
                stored_archive.storage_path
            ),

            archive_size_bytes=(
                stored_archive.size_bytes
            ),

            archive_sha256=(
                stored_archive.sha256
            ),

            archive_entry_count=(
                stored_archive.entry_count
            ),

            archive_uncompressed_bytes=(
                stored_archive.uncompressed_bytes
            ),

            environment_id=data[
                "environment_id"
            ],

            user_id=user_id,
        )

    except ArchiveProviderError as error:
        raise ProjectServiceError(
            error.code,
            error.message,
            error.http_status,
        ) from error

    except Exception:
        if stored_archive is not None:
            archive_source_provider.remove_stored_archive(
                stored_archive.storage_path
            )

        raise

    save_source_check(
        project_id=int(project["id"]),
        user_id=user_id,
        source_connection_id=None,

        repository_path=(
            stored_archive.original_name
        ),

        branch="archive",
        status="valid",
        commit_sha=None,

        error_code=None,
        error_message=None,

        details={
            **stored_archive.to_dict(),

            "operationMode": data[
                "operation_mode"
            ],

            "environmentId": data[
                "environment_id"
            ],
        },
    )

    return {
        "project": project,

        "sourceValidation":
            stored_archive.to_dict(),
    }