from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from flask import g, jsonify, request
from werkzeug.datastructures import FileStorage

from app.auth.decorators import (
    current_user_is_admin,
    require_auth,
    require_project_access,
)
from app.database import get_database_connection
from app.integrations.security import decrypt_credential, encrypt_credential
from app.projects.archive_provider import ArchiveProviderError, archive_source_provider
from app.projects.repository import (
    PROJECT_SELECT,
    add_project_activity,
    create_slug,
    find_available_slug,
    read_project_in_transaction,
    save_source_check,
)
from app.projects.routes import (
    error_response,
    project_json,
    projects_blueprint,
    read_request_payload,
    source_validation_json,
)
from app.projects.service import ProjectServiceError, ensure_project_create_role
from app.projects.source_provider import SourceProviderError, git_source_provider
from app.projects.validators import (
    ProjectValidationError,
    normalize_optional_text,
    normalize_text,
    positive_integer,
    read_operation_mode,
    read_source_payload,
    validate_description,
    validate_project_name,
)


REQUIRED_ENVIRONMENT_ROLES = {
    "kubernetes",
    "argocd",
    "container_registry",
    "gitops_repository",
}

ALLOWED_GIT_PROVIDER_TYPES = {
    "gitlab",
    "github",
    "git",
}


class ProjectDraftError(RuntimeError):
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


def _user_id() -> int:
    return int(g.current_user["id"])


def _roles() -> set[str]:
    return set(g.current_user.get("roles") or [])


def _owner_user_id() -> int | None:
    return None if current_user_is_admin() else _user_id()


def _json_object() -> dict[str, Any]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ProjectValidationError(
            "INVALID_JSON",
            "Le corps JSON est invalide.",
        )
    return payload


def _find_project(project_id: int) -> dict[str, Any] | None:
    with get_database_connection() as connection:
        return connection.execute(
            f"""
                {PROJECT_SELECT}
                WHERE project.id = %s
                  AND project.archived_at IS NULL
                LIMIT 1;
            """,
            (project_id,),
        ).fetchone()


def _find_project_source(project_id: int) -> dict[str, Any] | None:
    query = """
        SELECT
            project.id,
            project.name,
            project.slug,
            project.status,
            project.source_type,
            project.source_provider,
            project.source_connection_id,
            project.repository_url,
            project.repository_path,
            project.repository_visibility,
            project.source_transport,
            project.source_credential_source,
            project.source_auth_method,
            project.source_token_type,
            project.source_username,
            project.default_branch,
            project.source_subdirectory,
            project.source_status,
            project.archive_original_name,
            project.archive_stored_name,
            project.archive_storage_path,
            project.archive_size_bytes,
            project.archive_sha256,
            project.archive_entry_count,
            project.archive_uncompressed_bytes,
            project.default_environment_id,
            project_credential.secret_ciphertext AS project_secret_ciphertext
        FROM projects AS project
        LEFT JOIN project_source_credentials AS project_credential
          ON project_credential.project_id = project.id
        WHERE project.id = %s
          AND project.archived_at IS NULL
        LIMIT 1;
    """
    with get_database_connection() as connection:
        return connection.execute(query, (project_id,)).fetchone()


def _find_git_connection(connection_id: int) -> dict[str, Any] | None:
    query = """
        SELECT
            connection.id,
            connection.name,
            connection.provider_type,
            connection.base_url,
            connection.verify_ssl,
            connection.ssh_host,
            connection.ssh_port,
            connection.ssh_username,
            connection.enabled,
            connection.status,
            COALESCE(credential.auth_type, 'none') AS credential_auth_type,
            credential.username AS credential_username,
            credential.secret_ciphertext,
            (credential.secret_ciphertext IS NOT NULL) AS credential_configured
        FROM integration_connections AS connection
        LEFT JOIN integration_credentials AS credential
          ON credential.connection_id = connection.id
        WHERE connection.id = %s
          AND connection.enabled = TRUE
          AND connection.provider_type IN ('gitlab', 'github', 'git')
          AND (
              %s::BIGINT IS NULL
              OR connection.created_by = %s
          )
        LIMIT 1;
    """
    with get_database_connection() as connection:
        return connection.execute(
            query,
            (connection_id, _owner_user_id(), _owner_user_id()),
        ).fetchone()


def _find_environment(environment_id: int) -> dict[str, Any] | None:
    query = """
        SELECT
            environment.id,
            environment.name,
            environment.code,
            environment.environment_type,
            environment.namespace,
            environment.domain,
            environment.configuration_status,
            COALESCE(
                ARRAY_AGG(link.service_role)
                    FILTER (WHERE link.service_role IS NOT NULL),
                ARRAY[]::TEXT[]
            ) AS service_roles
        FROM deployment_environments AS environment
        LEFT JOIN environment_connections AS link
          ON link.environment_id = environment.id
        WHERE environment.id = %s
          AND environment.configuration_status <> 'archived'
          AND (
              %s::BIGINT IS NULL
              OR environment.created_by = %s
          )
        GROUP BY environment.id
        LIMIT 1;
    """
    with get_database_connection() as connection:
        return connection.execute(
            query,
            (environment_id, _owner_user_id(), _owner_user_id()),
        ).fetchone()


def _ensure_draft_editable(project: dict[str, Any]) -> None:
    if project["status"] not in {"draft", "source_error"}:
        raise ProjectDraftError(
            "PROJECT_NOT_EDITABLE",
            "La configuration initiale de ce projet est déjà terminée.",
            409,
        )


def _resolve_stored_credential(
    *,
    project: dict[str, Any],
    connection: dict[str, Any],
) -> tuple[str | None, str | None]:
    visibility = project.get("repository_visibility") or "private"
    if visibility == "public":
        return None, None

    credential_source = project.get("source_credential_source") or "none"
    if credential_source == "integration":
        ciphertext = connection.get("secret_ciphertext")
        if not ciphertext:
            raise ProjectDraftError(
                "INTEGRATION_CREDENTIAL_NOT_CONFIGURED",
                "La connexion Git ne contient aucun credential.",
            )
        username = connection.get("credential_username")
        if not username and connection.get("credential_auth_type") == "token":
            username = (
                "x-access-token"
                if connection.get("provider_type") == "github"
                else "oauth2"
            )
        return username, decrypt_credential(ciphertext)

    if credential_source == "project":
        ciphertext = project.get("project_secret_ciphertext")
        if not ciphertext:
            raise ProjectDraftError(
                "PROJECT_CREDENTIAL_NOT_CONFIGURED",
                "Le credential propre au projet n'est pas enregistré.",
            )
        return project.get("source_username"), decrypt_credential(ciphertext)

    raise ProjectDraftError(
        "SOURCE_CREDENTIAL_NOT_CONFIGURED",
        "Aucun credential n'est configuré pour cette source privée.",
    )


def _validate_git(
    *,
    data: dict[str, Any],
    connection: dict[str, Any],
    username: str | None,
    secret: str | None,
):
    try:
        return git_source_provider.validate_repository(
            connection=connection,
            repository_url=data["repository_url"],
            visibility=data["visibility"],
            transport=data["transport"],
            branch=data["branch"],
            username=username,
            secret=secret,
        )
    except SourceProviderError as error:
        raise ProjectDraftError(
            error.code,
            error.message,
            error.http_status,
        ) from error


def _prepare_git_payload(
    *,
    raw_payload: dict[str, Any],
    project: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    payload = dict(raw_payload)
    secret_reused = False

    credential_source = normalize_text(payload.get("credentialSource"))
    incoming_secret = normalize_optional_text(payload.get("secret"))

    if credential_source == "project" and not incoming_secret:
        stored_ciphertext = project.get("project_secret_ciphertext")
        same_project_credential = (
            project.get("source_credential_source") == "project"
            and stored_ciphertext is not None
        )
        if same_project_credential:
            payload["secret"] = decrypt_credential(stored_ciphertext)
            secret_reused = True

    data = read_source_payload(payload)
    return data, secret_reused


def _credential_for_new_git_source(
    *,
    data: dict[str, Any],
    connection: dict[str, Any],
) -> tuple[str | None, str | None]:
    if data["visibility"] == "public":
        return None, None

    if data["credential_source"] == "integration":
        if not connection.get("credential_configured"):
            raise ProjectDraftError(
                "INTEGRATION_CREDENTIAL_NOT_CONFIGURED",
                "La connexion Git ne contient aucun credential.",
            )

        integration_auth = connection.get("credential_auth_type")
        if data["transport"] == "ssh" and integration_auth != "ssh_key":
            raise ProjectDraftError(
                "INTEGRATION_TRANSPORT_MISMATCH",
                "La connexion ne contient pas une clé privée SSH.",
            )
        if data["transport"] == "https" and integration_auth == "ssh_key":
            raise ProjectDraftError(
                "INTEGRATION_TRANSPORT_MISMATCH",
                "La connexion contient une clé SSH alors que HTTPS est sélectionné.",
            )

        ciphertext = connection.get("secret_ciphertext")
        if not ciphertext:
            raise ProjectDraftError(
                "INTEGRATION_CREDENTIAL_NOT_CONFIGURED",
                "Le secret de la connexion Git est absent.",
            )

        username = connection.get("credential_username")
        if not username and connection.get("credential_auth_type") == "token":
            username = (
                "x-access-token"
                if connection.get("provider_type") == "github"
                else "oauth2"
            )

        return username, decrypt_credential(ciphertext)

    return data.get("username"), data.get("secret")


def _save_git_source(
    *,
    project_id: int,
    project: dict[str, Any],
    raw_payload: dict[str, Any],
    user_id: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    data, secret_reused = _prepare_git_payload(
        raw_payload=raw_payload,
        project=project,
    )

    connection = _find_git_connection(data["source_connection_id"])
    if connection is None:
        raise ProjectDraftError(
            "INVALID_GIT_CONNECTION",
            "La connexion Git sélectionnée est introuvable ou désactivée.",
        )

    username, secret = _credential_for_new_git_source(
        data=data,
        connection=connection,
    )
    validation = _validate_git(
        data=data,
        connection=connection,
        username=username,
        secret=secret,
    )

    encrypted_secret: str | None = None
    if data["credential_source"] == "project":
        if secret_reused:
            encrypted_secret = project.get("project_secret_ciphertext")
        else:
            encrypted_secret = encrypt_credential(secret)

    with get_database_connection() as database_connection:
        database_connection.execute(
            """
                UPDATE projects
                SET
                    source_type = 'git',
                    source_provider = %s,
                    source_connection_id = %s,
                    repository_url = %s,
                    repository_path = %s,
                    repository_visibility = %s,
                    source_transport = %s,
                    source_credential_source = %s,
                    source_auth_method = %s,
                    source_token_type = %s,
                    source_username = %s,
                    default_branch = %s,
                    source_subdirectory = %s,
                    archive_original_name = NULL,
                    archive_stored_name = NULL,
                    archive_storage_path = NULL,
                    archive_size_bytes = NULL,
                    archive_sha256 = NULL,
                    archive_entry_count = NULL,
                    archive_uncompressed_bytes = NULL,
                    source_status = 'valid',
                    source_error = NULL,
                    last_source_commit_sha = %s,
                    last_source_check_at = CURRENT_TIMESTAMP,
                    status = 'draft',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s;
            """,
            (
                connection["provider_type"],
                data["source_connection_id"],
                validation.repository_url,
                validation.repository_path,
                validation.visibility,
                validation.transport,
                data["credential_source"],
                data["auth_method"],
                data["token_type"],
                data["username"],
                validation.branch,
                data["source_subdirectory"],
                validation.commit_sha,
                project_id,
            ),
        )

        if data["credential_source"] == "project":
            database_connection.execute(
                """
                    INSERT INTO project_source_credentials (
                        project_id,
                        secret_ciphertext
                    )
                    VALUES (%s, %s)
                    ON CONFLICT (project_id)
                    DO UPDATE SET
                        secret_ciphertext = EXCLUDED.secret_ciphertext,
                        updated_at = CURRENT_TIMESTAMP;
                """,
                (project_id, encrypted_secret),
            )
        else:
            database_connection.execute(
                "DELETE FROM project_source_credentials WHERE project_id = %s;",
                (project_id,),
            )

        add_project_activity(
            database_connection=database_connection,
            project_id=project_id,
            user_id=user_id,
            action="project.source.saved",
            details={
                "sourceType": "git",
                "connectionId": data["source_connection_id"],
                "repositoryPath": validation.repository_path,
                "branch": validation.branch,
                "commitSha": validation.commit_sha,
                "credentialSource": data["credential_source"],
                "credentialReused": secret_reused,
            },
        )

        saved_project = read_project_in_transaction(
            database_connection=database_connection,
            project_id=project_id,
        )

    save_source_check(
        project_id=project_id,
        user_id=user_id,
        source_connection_id=data["source_connection_id"],
        repository_path=validation.repository_path,
        branch=validation.branch,
        status="valid",
        commit_sha=validation.commit_sha,
        error_code=None,
        error_message=None,
        details={
            **validation.to_dict(),
            "credentialSource": data["credential_source"],
            "authMethod": data["auth_method"],
        },
    )

    return saved_project, validation.to_dict()


def _save_zip_source(
    *,
    project_id: int,
    project: dict[str, Any],
    raw_payload: dict[str, Any],
    archive_file: FileStorage | None,
    user_id: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    data = read_source_payload(raw_payload)
    old_storage_path = project.get("archive_storage_path")

    try:
        stored_archive = archive_source_provider.store_upload(archive_file)
    except ArchiveProviderError as error:
        raise ProjectDraftError(
            error.code,
            error.message,
            error.http_status,
        ) from error

    if not stored_archive.storage_path or not stored_archive.stored_name:
        raise ProjectDraftError(
            "ARCHIVE_STORAGE_FAILED",
            "L'archive n'a pas pu être enregistrée.",
            500,
        )

    try:
        with get_database_connection() as database_connection:
            database_connection.execute(
                """
                    UPDATE projects
                    SET
                        source_type = 'zip',
                        source_provider = 'archive',
                        source_connection_id = NULL,
                        repository_url = NULL,
                        repository_path = NULL,
                        repository_visibility = NULL,
                        source_transport = NULL,
                        source_credential_source = NULL,
                        source_auth_method = NULL,
                        source_token_type = NULL,
                        source_username = NULL,
                        default_branch = NULL,
                        source_subdirectory = %s,
                        archive_original_name = %s,
                        archive_stored_name = %s,
                        archive_storage_path = %s,
                        archive_size_bytes = %s,
                        archive_sha256 = %s,
                        archive_entry_count = %s,
                        archive_uncompressed_bytes = %s,
                        source_status = 'valid',
                        source_error = NULL,
                        last_source_commit_sha = NULL,
                        last_source_check_at = CURRENT_TIMESTAMP,
                        status = 'draft',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s;
                """,
                (
                    data["source_subdirectory"],
                    stored_archive.original_name,
                    stored_archive.stored_name,
                    stored_archive.storage_path,
                    stored_archive.size_bytes,
                    stored_archive.sha256,
                    stored_archive.entry_count,
                    stored_archive.uncompressed_bytes,
                    project_id,
                ),
            )
            database_connection.execute(
                "DELETE FROM project_source_credentials WHERE project_id = %s;",
                (project_id,),
            )
            add_project_activity(
                database_connection=database_connection,
                project_id=project_id,
                user_id=user_id,
                action="project.source.saved",
                details={
                    "sourceType": "zip",
                    "archiveName": stored_archive.original_name,
                    "archiveSha256": stored_archive.sha256,
                },
            )
            saved_project = read_project_in_transaction(
                database_connection=database_connection,
                project_id=project_id,
            )
    except Exception:
        archive_source_provider.remove_stored_archive(stored_archive.storage_path)
        raise

    if old_storage_path and old_storage_path != stored_archive.storage_path:
        archive_source_provider.remove_stored_archive(old_storage_path)

    validation = stored_archive.to_dict()
    save_source_check(
        project_id=project_id,
        user_id=user_id,
        source_connection_id=None,
        repository_path=stored_archive.original_name,
        branch="archive",
        status="valid",
        commit_sha=None,
        error_code=None,
        error_message=None,
        details=validation,
    )
    return saved_project, validation


def _stored_zip_validation(project: dict[str, Any]) -> dict[str, Any]:
    storage_path = project.get("archive_storage_path")
    if not storage_path or not Path(storage_path).is_file():
        raise ProjectDraftError(
            "ARCHIVE_NOT_FOUND",
            "L'archive enregistrée est introuvable sur le stockage.",
            404,
        )
    return {
        "source_type": "zip",
        "original_name": project.get("archive_original_name"),
        "stored_name": project.get("archive_stored_name"),
        "storage_path": storage_path,
        "size_bytes": project.get("archive_size_bytes") or 0,
        "sha256": project.get("archive_sha256") or "",
        "entry_count": project.get("archive_entry_count") or 0,
        "uncompressed_bytes": project.get("archive_uncompressed_bytes") or 0,
        "top_level_entries": [],
        "validation_method": "stored_archive_check",
    }


def _test_stored_source(
    *,
    project_id: int,
    user_id: int,
) -> dict[str, Any]:
    project = _find_project_source(project_id)
    if project is None:
        raise ProjectDraftError(
            "PROJECT_NOT_FOUND",
            "Le projet est introuvable.",
            404,
        )

    if project.get("source_type") == "zip":
        validation = _stored_zip_validation(project)
        with get_database_connection() as connection:
            connection.execute(
                """
                    UPDATE projects
                    SET source_status = 'valid',
                        source_error = NULL,
                        last_source_check_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s;
                """,
                (project_id,),
            )
        return validation

    connection = _find_git_connection(int(project.get("source_connection_id") or 0))
    if connection is None:
        raise ProjectDraftError(
            "INVALID_GIT_CONNECTION",
            "La connexion Git du projet est introuvable ou désactivée.",
        )

    username, secret = _resolve_stored_credential(
        project=project,
        connection=connection,
    )
    data = {
        "repository_url": project.get("repository_url"),
        "visibility": project.get("repository_visibility") or "private",
        "transport": project.get("source_transport") or "https",
        "branch": project.get("default_branch") or "main",
    }

    try:
        validation = _validate_git(
            data=data,
            connection=connection,
            username=username,
            secret=secret,
        )
    except ProjectDraftError as error:
        with get_database_connection() as database_connection:
            database_connection.execute(
                """
                    UPDATE projects
                    SET source_status = %s,
                        source_error = %s,
                        last_source_check_at = CURRENT_TIMESTAMP,
                        status = CASE
                            WHEN status = 'active' THEN 'source_error'
                            ELSE status
                        END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s;
                """,
                (
                    "error" if error.http_status >= 500 else "invalid",
                    error.message,
                    project_id,
                ),
            )
        save_source_check(
            project_id=project_id,
            user_id=user_id,
            source_connection_id=project.get("source_connection_id"),
            repository_path=project.get("repository_path") or project.get("repository_url") or "",
            branch=project.get("default_branch") or "main",
            status="error" if error.http_status >= 500 else "invalid",
            commit_sha=None,
            error_code=error.code,
            error_message=error.message,
            details={"storedCredential": True},
        )
        raise

    with get_database_connection() as database_connection:
        database_connection.execute(
            """
                UPDATE projects
                SET source_status = 'valid',
                    source_error = NULL,
                    last_source_commit_sha = %s,
                    last_source_check_at = CURRENT_TIMESTAMP,
                    status = CASE
                        WHEN status = 'source_error' THEN 'active'
                        ELSE status
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s;
            """,
            (validation.commit_sha, project_id),
        )

    save_source_check(
        project_id=project_id,
        user_id=user_id,
        source_connection_id=project.get("source_connection_id"),
        repository_path=validation.repository_path,
        branch=validation.branch,
        status="valid",
        commit_sha=validation.commit_sha,
        error_code=None,
        error_message=None,
        details={**validation.to_dict(), "storedCredential": True},
    )
    return validation.to_dict()


def _validate_replacement_payload(
    payload: dict[str, Any],
    project: dict[str, Any],
) -> dict[str, Any]:
    credential_source = normalize_text(payload.get("credentialSource"))
    auth_method = normalize_text(payload.get("authMethod"))
    token_type = normalize_optional_text(payload.get("tokenType"))
    username = normalize_optional_text(payload.get("username"))
    secret = normalize_optional_text(payload.get("secret"))

    if credential_source not in {"integration", "project", "none"}:
        raise ProjectValidationError(
            "INVALID_CREDENTIAL_SOURCE",
            "L'origine du credential est invalide.",
        )

    visibility = project.get("repository_visibility") or "private"
    transport = project.get("source_transport") or "https"
    if visibility == "public":
        return {
            "credential_source": "none",
            "auth_method": "none",
            "token_type": None,
            "username": None,
            "secret": None,
        }

    if credential_source == "integration":
        return {
            "credential_source": "integration",
            "auth_method": "none",
            "token_type": None,
            "username": None,
            "secret": None,
        }

    if credential_source != "project" or not secret:
        raise ProjectValidationError(
            "PROJECT_SECRET_REQUIRED",
            "Saisissez le nouveau token, mot de passe ou la nouvelle clé privée.",
        )

    if transport == "ssh":
        if auth_method != "ssh_key" or "PRIVATE KEY" not in secret:
            raise ProjectValidationError(
                "INVALID_SSH_PRIVATE_KEY",
                "La clé privée SSH est invalide.",
            )
        return {
            "credential_source": "project",
            "auth_method": "ssh_key",
            "token_type": None,
            "username": None,
            "secret": secret,
        }

    if auth_method not in {"https_token", "https_password"} or not username:
        raise ProjectValidationError(
            "INVALID_HTTPS_CREDENTIAL",
            "Le username et la méthode HTTPS sont obligatoires.",
        )

    allowed_tokens = {
        "personal_access_token",
        "project_access_token",
        "group_access_token",
        "deploy_token",
        "generic_token",
    }
    if auth_method == "https_token" and token_type not in allowed_tokens:
        raise ProjectValidationError(
            "INVALID_TOKEN_TYPE",
            "Le type de token est invalide.",
        )

    return {
        "credential_source": "project",
        "auth_method": auth_method,
        "token_type": token_type if auth_method == "https_token" else None,
        "username": username,
        "secret": secret,
    }


def _replace_credential(
    *,
    project_id: int,
    payload: dict[str, Any],
    user_id: int,
) -> dict[str, Any]:
    project = _find_project_source(project_id)
    if project is None:
        raise ProjectDraftError("PROJECT_NOT_FOUND", "Le projet est introuvable.", 404)
    if project.get("source_type") != "git":
        raise ProjectDraftError(
            "CREDENTIAL_NOT_APPLICABLE",
            "Une archive ZIP n'utilise pas de credential Git.",
        )

    credential = _validate_replacement_payload(payload, project)
    connection = _find_git_connection(int(project.get("source_connection_id") or 0))
    if connection is None:
        raise ProjectDraftError(
            "INVALID_GIT_CONNECTION",
            "La connexion Git du projet est introuvable ou désactivée.",
        )

    if credential["credential_source"] == "integration":
        if not connection.get("credential_configured"):
            raise ProjectDraftError(
                "INTEGRATION_CREDENTIAL_NOT_CONFIGURED",
                "La connexion Git ne contient aucun credential.",
            )
        username = connection.get("credential_username")
        if not username and connection.get("credential_auth_type") == "token":
            username = (
                "x-access-token"
                if connection.get("provider_type") == "github"
                else "oauth2"
            )
        secret = decrypt_credential(connection["secret_ciphertext"])
    else:
        username = credential.get("username")
        secret = credential.get("secret")

    validation = _validate_git(
        data={
            "repository_url": project["repository_url"],
            "visibility": project.get("repository_visibility") or "private",
            "transport": project.get("source_transport") or "https",
            "branch": project.get("default_branch") or "main",
        },
        connection=connection,
        username=username,
        secret=secret,
    )

    with get_database_connection() as database_connection:
        database_connection.execute(
            """
                UPDATE projects
                SET source_credential_source = %s,
                    source_auth_method = %s,
                    source_token_type = %s,
                    source_username = %s,
                    source_status = 'valid',
                    source_error = NULL,
                    last_source_commit_sha = %s,
                    last_source_check_at = CURRENT_TIMESTAMP,
                    status = CASE
                        WHEN status = 'source_error' THEN 'active'
                        ELSE status
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s;
            """,
            (
                credential["credential_source"],
                credential["auth_method"],
                credential["token_type"],
                credential["username"],
                validation.commit_sha,
                project_id,
            ),
        )

        if credential["credential_source"] == "project":
            database_connection.execute(
                """
                    INSERT INTO project_source_credentials (
                        project_id,
                        secret_ciphertext
                    )
                    VALUES (%s, %s)
                    ON CONFLICT (project_id)
                    DO UPDATE SET
                        secret_ciphertext = EXCLUDED.secret_ciphertext,
                        updated_at = CURRENT_TIMESTAMP;
                """,
                (project_id, encrypt_credential(credential["secret"])),
            )
        else:
            database_connection.execute(
                "DELETE FROM project_source_credentials WHERE project_id = %s;",
                (project_id,),
            )

        add_project_activity(
            database_connection=database_connection,
            project_id=project_id,
            user_id=user_id,
            action="project.source.credential_replaced",
            details={
                "credentialSource": credential["credential_source"],
                "authMethod": credential["auth_method"],
                "commitSha": validation.commit_sha,
            },
        )
        return read_project_in_transaction(
            database_connection=database_connection,
            project_id=project_id,
        )


def _environment_missing_roles(environment: dict[str, Any]) -> list[str]:
    configured = set(environment.get("service_roles") or [])
    return sorted(REQUIRED_ENVIRONMENT_ROLES - configured)


@projects_blueprint.post("/drafts")
@require_auth
def create_project_draft_route():
    try:
        ensure_project_create_role(_roles())
        payload = _json_object()
        operation_mode = read_operation_mode(payload)
        name = validate_project_name(payload.get("name"))
        description = validate_description(payload.get("description"))

        with get_database_connection() as connection:
            slug = find_available_slug(
                database_connection=connection,
                slug_base=create_slug(name),
            )
            row = connection.execute(
                """
                    INSERT INTO projects (
                        name,
                        slug,
                        description,
                        operation_mode,
                        source_type,
                        source_provider,
                        repository_visibility,
                        source_transport,
                        source_credential_source,
                        source_auth_method,
                        default_branch,
                        source_status,
                        status,
                        created_by
                    )
                    VALUES (
                        %s, %s, %s, %s,
                        'git', 'gitlab', 'private', 'https',
                        'none', 'none', 'main', 'unchecked',
                        'draft', %s
                    )
                    RETURNING id;
                """,
                (name, slug, description, operation_mode, _user_id()),
            ).fetchone()
            if row is None:
                raise ProjectDraftError(
                    "PROJECT_DRAFT_CREATE_FAILED",
                    "Le brouillon du projet n'a pas pu être créé.",
                    500,
                )
            project_id = int(row["id"])
            add_project_activity(
                database_connection=connection,
                project_id=project_id,
                user_id=_user_id(),
                action="project.draft.created",
                details={"operationMode": operation_mode},
            )
            project = read_project_in_transaction(
                database_connection=connection,
                project_id=project_id,
            )

        return jsonify({"success": True, "data": {"project": project_json(project)}}), 201

    except ProjectValidationError as error:
        return error_response(error.code, error.message, 400)
    except ProjectServiceError as error:
        return error_response(error.code, error.message, error.http_status)
    except ProjectDraftError as error:
        return error_response(error.code, error.message, error.http_status)


@projects_blueprint.put("/<int:project_id>/source")
@require_auth
@require_project_access
def save_project_draft_source_route(project_id: int):
    try:
        payload, archive_file = read_request_payload()
        project = _find_project_source(project_id)
        if project is None:
            raise ProjectDraftError("PROJECT_NOT_FOUND", "Le projet est introuvable.", 404)
        _ensure_draft_editable(project)

        source_type = normalize_text(payload.get("sourceType")) or "git"
        if source_type == "zip":
            saved_project, validation = _save_zip_source(
                project_id=project_id,
                project=project,
                raw_payload=payload,
                archive_file=archive_file,
                user_id=_user_id(),
            )
        else:
            saved_project, validation = _save_git_source(
                project_id=project_id,
                project=project,
                raw_payload=payload,
                user_id=_user_id(),
            )

        return jsonify(
            {
                "success": True,
                "data": {
                    "project": project_json(saved_project),
                    "sourceValidation": source_validation_json(validation),
                },
            }
        )

    except ProjectValidationError as error:
        return error_response(error.code, error.message, 400)
    except ProjectDraftError as error:
        return error_response(error.code, error.message, error.http_status)


@projects_blueprint.post("/<int:project_id>/source/check")
@require_auth
@require_project_access
def check_stored_project_source_route(project_id: int):
    try:
        validation = _test_stored_source(project_id=project_id, user_id=_user_id())
        return jsonify(
            {
                "success": True,
                "data": {"sourceValidation": source_validation_json(validation)},
            }
        )
    except ProjectDraftError as error:
        return error_response(error.code, error.message, error.http_status)


@projects_blueprint.put("/<int:project_id>/source/credential")
@require_auth
@require_project_access
def replace_project_source_credential_route(project_id: int):
    try:
        project = _replace_credential(
            project_id=project_id,
            payload=_json_object(),
            user_id=_user_id(),
        )
        return jsonify({"success": True, "data": {"project": project_json(project)}})
    except ProjectValidationError as error:
        return error_response(error.code, error.message, 400)
    except ProjectDraftError as error:
        return error_response(error.code, error.message, error.http_status)


@projects_blueprint.put("/<int:project_id>/environment")
@require_auth
@require_project_access
def save_project_environment_route(project_id: int):
    try:
        payload = _json_object()
        environment_id = positive_integer(payload.get("environmentId"), "environmentId")
        project = _find_project(project_id)
        if project is None:
            raise ProjectDraftError("PROJECT_NOT_FOUND", "Le projet est introuvable.", 404)
        _ensure_draft_editable(project)

        environment = _find_environment(environment_id)
        if environment is None:
            raise ProjectDraftError(
                "INVALID_ENVIRONMENT",
                "L'environnement sélectionné est introuvable ou archivé.",
            )
        missing_roles = _environment_missing_roles(environment)
        if missing_roles:
            raise ProjectDraftError(
                "ENVIRONMENT_INCOMPLETE",
                "L'environnement ne contient pas tous les services requis : "
                + ", ".join(missing_roles),
            )

        with get_database_connection() as connection:
            connection.execute(
                "UPDATE project_environments SET is_default = FALSE WHERE project_id = %s;",
                (project_id,),
            )
            connection.execute(
                """
                    INSERT INTO project_environments (
                        project_id,
                        environment_id,
                        is_default
                    )
                    VALUES (%s, %s, TRUE)
                    ON CONFLICT (project_id, environment_id)
                    DO UPDATE SET is_default = TRUE;
                """,
                (project_id, environment_id),
            )
            connection.execute(
                """
                    UPDATE projects
                    SET default_environment_id = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s;
                """,
                (environment_id, project_id),
            )
            add_project_activity(
                database_connection=connection,
                project_id=project_id,
                user_id=_user_id(),
                action="project.environment.selected",
                details={
                    "environmentId": environment_id,
                    "environmentName": environment["name"],
                },
            )
            saved_project = read_project_in_transaction(
                database_connection=connection,
                project_id=project_id,
            )

        return jsonify({"success": True, "data": {"project": project_json(saved_project)}})

    except ProjectValidationError as error:
        return error_response(error.code, error.message, 400)
    except ProjectDraftError as error:
        return error_response(error.code, error.message, error.http_status)


@projects_blueprint.post("/<int:project_id>/activate")
@require_auth
@require_project_access
def activate_project_route(project_id: int):
    try:
        project = _find_project(project_id)
        if project is None:
            raise ProjectDraftError("PROJECT_NOT_FOUND", "Le projet est introuvable.", 404)
        _ensure_draft_editable(project)

        if project.get("source_status") != "valid":
            raise ProjectDraftError(
                "SOURCE_NOT_VALIDATED",
                "La source doit être vérifiée avant l'activation du projet.",
            )
        if not project.get("default_environment_id"):
            raise ProjectDraftError(
                "ENVIRONMENT_REQUIRED",
                "Sélectionnez un environnement avant d'activer le projet.",
            )
        if (
            project.get("source_type") == "git"
            and project.get("repository_visibility") == "private"
            and not project.get("source_credential_configured")
        ):
            raise ProjectDraftError(
                "SOURCE_CREDENTIAL_NOT_CONFIGURED",
                "Le credential de la source privée n'est pas enregistré.",
            )

        environment = _find_environment(int(project["default_environment_id"]))
        if environment is None:
            raise ProjectDraftError(
                "INVALID_ENVIRONMENT",
                "L'environnement du projet est introuvable ou archivé.",
            )
        missing_roles = _environment_missing_roles(environment)
        if missing_roles:
            raise ProjectDraftError(
                "ENVIRONMENT_INCOMPLETE",
                "L'environnement ne contient pas tous les services requis : "
                + ", ".join(missing_roles),
            )

        with get_database_connection() as connection:
            connection.execute(
                """
                    UPDATE projects
                    SET status = 'active',
                        source_error = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s;
                """,
                (project_id,),
            )
            add_project_activity(
                database_connection=connection,
                project_id=project_id,
                user_id=_user_id(),
                action="project.activated",
                details={"environmentId": project["default_environment_id"]},
            )
            activated = read_project_in_transaction(
                database_connection=connection,
                project_id=project_id,
            )

        return jsonify({"success": True, "data": {"project": project_json(activated)}})

    except ProjectDraftError as error:
        return error_response(error.code, error.message, error.http_status)