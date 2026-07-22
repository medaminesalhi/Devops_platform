from __future__ import annotations

import re

from typing import Any


VISIBILITIES = {
    "public",
    "private",
}

TRANSPORTS = {
    "https",
    "ssh",
}

CREDENTIAL_SOURCES = {
    "none",
    "integration",
    "project",
}

AUTH_METHODS = {
    "none",
    "https_password",
    "https_token",
    "ssh_key",
}

TOKEN_TYPES = {
    "personal_access_token",
    "project_access_token",
    "group_access_token",
    "deploy_token",
    "generic_token",
}


class ProjectValidationError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
    ) -> None:
        super().__init__(message)

        self.code = code
        self.message = message


def normalize_text(
    value: Any,
) -> str:
    return str(value or "").strip()


def normalize_optional_text(
    value: Any,
) -> str | None:
    value = normalize_text(value)

    return value or None


def positive_integer(
    value: Any,
    field_name: str,
) -> int:
    try:
        identifier = int(value)

    except (TypeError, ValueError) as error:
        raise ProjectValidationError(
            "INVALID_IDENTIFIER",
            f"Le champ {field_name} est invalide.",
        ) from error

    if identifier <= 0:
        raise ProjectValidationError(
            "INVALID_IDENTIFIER",
            f"Le champ {field_name} est invalide.",
        )

    return identifier


def identifier_list(
    value: Any,
    field_name: str,
) -> list[int]:
    if not isinstance(value, list):
        raise ProjectValidationError(
            "INVALID_IDENTIFIER_LIST",
            f"Le champ {field_name} doit être une liste.",
        )

    result: list[int] = []

    for item in value:
        identifier = positive_integer(
            item,
            field_name,
        )

        if identifier not in result:
            result.append(identifier)

    return result


def validate_repository_url(
    value: Any,
) -> str:
    repository_url = normalize_text(value)

    if not repository_url:
        raise ProjectValidationError(
            "REPOSITORY_URL_REQUIRED",
            "L'URL de clonage est obligatoire.",
        )

    if len(repository_url) > 1500:
        raise ProjectValidationError(
            "REPOSITORY_URL_TOO_LONG",
            "L'URL de clonage est trop longue.",
        )

    if re.match(
        r"^https://[^/\s]+@",
        repository_url,
    ):
        raise ProjectValidationError(
            "CREDENTIAL_IN_URL_NOT_ALLOWED",
            (
                "Ne placez pas le username, "
                "le mot de passe ou le token dans l'URL."
            ),
        )

    is_https = repository_url.startswith(
        "https://"
    )

    is_ssh = (
        repository_url.startswith("ssh://")
        or bool(
            re.match(
                r"^[^@\s]+@[^:\s]+:.+$",
                repository_url,
            )
        )
    )

    if not is_https and not is_ssh:
        raise ProjectValidationError(
            "INVALID_REPOSITORY_URL",
            (
                "Utilisez une URL HTTPS ou SSH "
                "copiée depuis GitLab."
            ),
        )

    return repository_url


def validate_branch(
    value: Any,
) -> str:
    branch = normalize_text(value) or "main"

    forbidden = {
        " ",
        "~",
        "^",
        ":",
        "?",
        "*",
        "[",
        "\\",
    }

    if (
        len(branch) > 255
        or branch.startswith("-")
        or branch.endswith(".")
        or branch.endswith(".lock")
        or ".." in branch
        or "@{" in branch
        or any(
            character in branch
            for character in forbidden
        )
    ):
        raise ProjectValidationError(
            "INVALID_BRANCH",
            "Le nom de la branche est invalide.",
        )

    return branch


def validate_subdirectory(
    value: Any,
) -> str | None:
    subdirectory = normalize_optional_text(
        value
    )

    if subdirectory is None:
        return None

    normalized = (
        subdirectory
        .replace("\\", "/")
        .strip("/")
    )

    if (
        not normalized
        or ".." in normalized.split("/")
        or re.match(
            r"^[a-zA-Z]:",
            normalized,
        )
        or len(normalized) > 500
    ):
        raise ProjectValidationError(
            "INVALID_SUBDIRECTORY",
            "Le sous-dossier est invalide.",
        )

    return normalized


def read_source_payload(
    payload: dict[str, Any],
) -> dict[str, Any]:
    visibility = normalize_text(
        payload.get("visibility")
    )

    transport = normalize_text(
        payload.get("transport")
    )

    credential_source = normalize_text(
        payload.get("credentialSource")
    )

    auth_method = normalize_text(
        payload.get("authMethod")
    )

    token_type = normalize_optional_text(
        payload.get("tokenType")
    )

    username = normalize_optional_text(
        payload.get("username")
    )

    secret = normalize_optional_text(
        payload.get("secret")
    )

    if visibility not in VISIBILITIES:
        raise ProjectValidationError(
            "INVALID_VISIBILITY",
            "La visibilité est invalide.",
        )

    if transport not in TRANSPORTS:
        raise ProjectValidationError(
            "INVALID_TRANSPORT",
            "Le transport Git est invalide.",
        )

    if credential_source not in CREDENTIAL_SOURCES:
        raise ProjectValidationError(
            "INVALID_CREDENTIAL_SOURCE",
            "L'origine du credential est invalide.",
        )

    if auth_method not in AUTH_METHODS:
        raise ProjectValidationError(
            "INVALID_AUTH_METHOD",
            "La méthode d'authentification est invalide.",
        )

    repository_url = validate_repository_url(
        payload.get("repositoryUrl")
    )

    detected_transport = (
        "https"
        if repository_url.startswith("https://")
        else "ssh"
    )

    if transport != detected_transport:
        raise ProjectValidationError(
            "TRANSPORT_URL_MISMATCH",
            (
                "Le transport choisi ne correspond "
                "pas à l'URL du repository."
            ),
        )

    if visibility == "public":
        if transport != "https":
            raise ProjectValidationError(
                "PUBLIC_REPOSITORY_HTTPS_REQUIRED",
                "Un repository public doit utiliser HTTPS.",
            )

        credential_source = "none"
        auth_method = "none"
        token_type = None
        username = None
        secret = None

    else:
        if credential_source == "none":
            raise ProjectValidationError(
                "PRIVATE_CREDENTIAL_REQUIRED",
                (
                    "Choisissez le credential de la connexion "
                    "ou fournissez un credential propre au projet."
                ),
            )

        if credential_source == "integration":
            auth_method = "none"
            token_type = None
            username = None
            secret = None

        elif transport == "https":
            if auth_method not in {
                "https_password",
                "https_token",
            }:
                raise ProjectValidationError(
                    "INVALID_HTTPS_AUTH_METHOD",
                    (
                        "Choisissez username/mot de passe "
                        "ou username/token."
                    ),
                )

            if not username:
                raise ProjectValidationError(
                    "GIT_USERNAME_REQUIRED",
                    "Le username Git est obligatoire.",
                )

            if not secret:
                raise ProjectValidationError(
                    "GIT_SECRET_REQUIRED",
                    (
                        "Le mot de passe ou le token "
                        "est obligatoire."
                    ),
                )

            if auth_method == "https_token":
                if token_type not in TOKEN_TYPES:
                    raise ProjectValidationError(
                        "INVALID_TOKEN_TYPE",
                        "Le type de token est invalide.",
                    )
            else:
                token_type = None

        else:
            auth_method = "ssh_key"
            token_type = None
            username = None

            if not secret:
                raise ProjectValidationError(
                    "SSH_PRIVATE_KEY_REQUIRED",
                    "La clé privée SSH est obligatoire.",
                )

            if (
                "PRIVATE KEY" not in secret
                or "BEGIN" not in secret
            ):
                raise ProjectValidationError(
                    "INVALID_SSH_PRIVATE_KEY",
                    "Le contenu de la clé privée SSH est invalide.",
                )

    return {
        "source_connection_id":
            positive_integer(
                payload.get("sourceConnectionId"),
                "sourceConnectionId",
            ),

        "repository_url":
            repository_url,

        "visibility":
            visibility,

        "transport":
            transport,

        "credential_source":
            credential_source,

        "auth_method":
            auth_method,

        "token_type":
            token_type,

        "username":
            username,

        "secret":
            secret,

        "branch":
            validate_branch(
                payload.get("branch")
            ),

        "source_subdirectory":
            validate_subdirectory(
                payload.get("sourceSubdirectory")
            ),
    }


def read_create_project_payload(
    payload: dict[str, Any],
) -> dict[str, Any]:
    data = read_source_payload(payload)

    name = normalize_text(
        payload.get("name")
    )

    if len(name) < 3 or len(name) > 140:
        raise ProjectValidationError(
            "INVALID_PROJECT_NAME",
            (
                "Le nom du projet doit contenir "
                "entre 3 et 140 caractères."
            ),
        )

    environment_ids = identifier_list(
        payload.get("allowedEnvironmentIds"),
        "allowedEnvironmentIds",
    )

    if not environment_ids:
        raise ProjectValidationError(
            "ENVIRONMENT_REQUIRED",
            (
                "Sélectionnez au moins "
                "un environnement."
            ),
        )

    default_environment_id = positive_integer(
        payload.get("defaultEnvironmentId"),
        "defaultEnvironmentId",
    )

    if (
        default_environment_id
        not in environment_ids
    ):
        environment_ids.append(
            default_environment_id
        )

    return {
        **data,

        "name":
            name,

        "description":
            normalize_optional_text(
                payload.get("description")
            ),

        "allowed_environment_ids":
            environment_ids,

        "default_environment_id":
            default_environment_id,
    }