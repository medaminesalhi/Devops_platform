from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from flask import (
    Blueprint,
    g,
    jsonify,
    request,
)

from app.auth.decorators import require_auth

from app.integrations.repository import (
    create_connection,
    delete_connection,
    find_connection,
    list_connections,
    update_connection,
)

from app.integrations.security import (
    decrypt_credential,
    encrypt_credential,
)

from app.integrations.service import (
    test_configuration,
    test_saved_connection,
)


integrations_blueprint = Blueprint(
    "integrations",
    __name__,
)


PROVIDER_TYPES = {
    "gitlab",
    "nexus",
    "argocd",
    "kubernetes",
    "ollama",
    "generic_http",
}


AUTH_TYPES = {
    "none",
    "token",
    "basic",
}


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


def can_manage_integrations() -> bool:
    """
    Seuls les administrateurs et les DevOps
    peuvent modifier ou supprimer une connexion.
    """

    roles = set(
        g.current_user.get("roles") or []
    )

    return bool(
        roles.intersection(
            {
                "admin",
                "devops",
            }
        )
    )


def connection_to_json(
    connection: dict[str, Any],
) -> dict[str, Any]:
    """
    Transforme une ligne PostgreSQL en JSON.

    secret_ciphertext n'est jamais retourné.
    """

    return {
        "id": connection["id"],
        "name": connection["name"],

        "providerType":
            connection["provider_type"],

        "baseUrl":
            connection["base_url"],

        "environment":
            connection["environment"],

        "description":
            connection["description"],

        "enabled":
            connection["enabled"],

        "monitoringEnabled":
            connection["monitoring_enabled"],

        "checkIntervalSeconds":
            connection[
                "check_interval_seconds"
            ],

        "failureThreshold":
            connection["failure_threshold"],

        "status":
            connection["status"],

        "consecutiveFailures":
            connection[
                "consecutive_failures"
            ],

        "lastHttpStatus":
            connection["last_http_status"],

        "lastError":
            connection["last_error"],

        "lastCheckedAt": (
            connection[
                "last_checked_at"
            ].isoformat()
            if connection[
                "last_checked_at"
            ]
            else None
        ),

        "lastLatencyMs":
            connection["last_latency_ms"],

        "authType":
            connection["auth_type"],

        "username":
            connection["username"],

        "credentialConfigured":
            connection[
                "credential_configured"
            ],

        "createdAt":
            connection[
                "created_at"
            ].isoformat(),

        "updatedAt":
            connection[
                "updated_at"
            ].isoformat(),
    }


def normalize_string(
    value: Any,
) -> str:
    return str(
        value or ""
    ).strip()


def normalize_url(
    value: Any,
) -> str:
    return normalize_string(
        value
    ).rstrip("/")


def validate_url(
    value: str,
) -> bool:
    parsed_url = urlparse(value)

    return (
        parsed_url.scheme
        in {"http", "https"}
        and bool(parsed_url.netloc)
    )


def read_payload(
    payload: dict[str, Any],
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Lit le formulaire Angular.

    La valeur environment n'est plus demandée
    dans l'interface. Elle reste en base pour
    compatibilité et reçoit "internal" par défaut.
    """

    provider_type = normalize_string(
        payload.get(
            "providerType",
            (
                existing["provider_type"]
                if existing
                else ""
            ),
        )
    )

    name = normalize_string(
        payload.get(
            "name",
            (
                existing["name"]
                if existing
                else ""
            ),
        )
    )

    base_url = normalize_url(
        payload.get(
            "baseUrl",
            (
                existing["base_url"]
                if existing
                else ""
            ),
        )
    )

    environment = (
        existing["environment"]
        if existing
        else "internal"
    )

    raw_description = payload.get(
        "description",
        (
            existing["description"]
            if existing
            else None
        ),
    )

    description = (
        normalize_string(raw_description)
        or None
    )

    auth_type = normalize_string(
        payload.get(
            "authType",
            (
                existing["auth_type"]
                if existing
                else "none"
            ),
        )
    )

    raw_username = payload.get(
        "username",
        (
            existing["username"]
            if existing
            else None
        ),
    )

    username = (
        normalize_string(raw_username)
        or None
    )

    raw_credential = payload.get(
        "credential"
    )

    credential = (
        normalize_string(raw_credential)
        or None
    )

    monitoring_enabled = payload.get(
        "monitoringEnabled",
        (
            existing[
                "monitoring_enabled"
            ]
            if existing
            else True
        ),
    )

    check_interval_seconds = int(
        payload.get(
            "checkIntervalSeconds",
            (
                existing[
                    "check_interval_seconds"
                ]
                if existing
                else 300
            ),
        )
    )

    failure_threshold = int(
        payload.get(
            "failureThreshold",
            (
                existing[
                    "failure_threshold"
                ]
                if existing
                else 3
            ),
        )
    )

    return {
        "name": name,
        "provider_type": provider_type,
        "base_url": base_url,
        "environment": environment,
        "description": description,
        "auth_type": auth_type,
        "username": username,
        "credential": credential,

        "monitoring_enabled":
            monitoring_enabled,

        "check_interval_seconds":
            check_interval_seconds,

        "failure_threshold":
            failure_threshold,
    }


def validate_configuration(
    configuration: dict[str, Any],
    existing: dict[str, Any] | None = None,
) -> str | None:
    if not configuration["name"]:
        return (
            "Le nom de la connexion "
            "est obligatoire."
        )

    if (
        configuration["provider_type"]
        not in PROVIDER_TYPES
    ):
        return (
            "Le fournisseur sélectionné "
            "n'est pas supporté."
        )

    if not validate_url(
        configuration["base_url"]
    ):
        return (
            "L'URL doit commencer par "
            "http:// ou https://."
        )

    if (
        configuration["auth_type"]
        not in AUTH_TYPES
    ):
        return (
            "Le type d'authentification "
            "n'est pas supporté."
        )

    if (
        configuration["auth_type"]
        == "basic"
        and not configuration["username"]
    ):
        return (
            "Le nom d'utilisateur est obligatoire "
            "pour l'authentification Basic."
        )

    has_existing_credential = bool(
        existing
        and existing[
            "credential_configured"
        ]
    )

    auth_type_changed = bool(
        existing
        and existing["auth_type"]
        != configuration["auth_type"]
    )

    if (
        configuration["auth_type"]
        != "none"

        and not configuration["credential"]

        and (
            not has_existing_credential
            or auth_type_changed
        )
    ):
        return (
            "Un token ou mot de passe "
            "est obligatoire."
        )

    if not isinstance(
        configuration["monitoring_enabled"],
        bool,
    ):
        return (
            "monitoringEnabled doit être "
            "un booléen."
        )

    if not (
        60
        <= configuration[
            "check_interval_seconds"
        ]
        <= 86400
    ):
        return (
            "L'intervalle doit être compris "
            "entre 60 et 86400 secondes."
        )

    if not (
        1
        <= configuration[
            "failure_threshold"
        ]
        <= 10
    ):
        return (
            "Le seuil d'échec doit être "
            "compris entre 1 et 10."
        )

    return None


def test_after_save(
    connection: dict[str, Any],
) -> tuple[
    dict[str, Any],
    dict[str, Any] | None,
    str | None,
]:
    """
    Lance automatiquement le contrôle après
    une création ou une modification.

    Une panne du service ne fait pas échouer
    l'enregistrement : le statut devient dégradé
    ou hors ligne.

    testError concerne uniquement une erreur
    interne empêchant le lancement du test.
    """

    try:
        (
            tested_connection,
            test_result,
        ) = test_saved_connection(
            int(connection["id"])
        )

        return (
            tested_connection,
            test_result,
            None,
        )

    except Exception as error:
        return (
            connection,
            None,
            str(error),
        )


@integrations_blueprint.get("")
@require_auth
def get_connections():
    connections = list_connections()

    return jsonify(
        {
            "success": True,
            "data": {
                "connections": [
                    connection_to_json(
                        connection
                    )
                    for connection
                    in connections
                ],
            },
        }
    )


@integrations_blueprint.get(
    "/<int:connection_id>"
)
@require_auth
def get_connection(
    connection_id: int,
):
    connection = find_connection(
        connection_id
    )

    if connection is None:
        return error_response(
            "CONNECTION_NOT_FOUND",
            "Connexion introuvable.",
            404,
        )

    return jsonify(
        {
            "success": True,
            "data": {
                "connection":
                    connection_to_json(
                        connection
                    ),
            },
        }
    )


@integrations_blueprint.post("")
@require_auth
def create_new_connection():
    if not can_manage_integrations():
        return error_response(
            "INSUFFICIENT_PERMISSIONS",
            (
                "Vous ne pouvez pas créer "
                "une intégration."
            ),
            403,
        )

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
        configuration = read_payload(
            payload
        )

    except (TypeError, ValueError):
        return error_response(
            "INVALID_CONFIGURATION",
            "La configuration contient "
            "une valeur invalide.",
            400,
        )

    validation_error = (
        validate_configuration(
            configuration
        )
    )

    if validation_error:
        return error_response(
            "INVALID_CONFIGURATION",
            validation_error,
            400,
        )

    secret_ciphertext = (
        encrypt_credential(
            configuration["credential"]
        )
        if configuration["credential"]
        else None
    )

    try:
        created_connection = create_connection(
            name=configuration["name"],

            provider_type=configuration[
                "provider_type"
            ],

            base_url=configuration[
                "base_url"
            ],

            environment=configuration[
                "environment"
            ],

            description=configuration[
                "description"
            ],

            auth_type=configuration[
                "auth_type"
            ],

            username=configuration[
                "username"
            ],

            secret_ciphertext=
                secret_ciphertext,

            monitoring_enabled=
                configuration[
                    "monitoring_enabled"
                ],

            check_interval_seconds=
                configuration[
                    "check_interval_seconds"
                ],

            failure_threshold=
                configuration[
                    "failure_threshold"
                ],

            user_id=int(
                g.current_user["id"]
            ),
        )

    except Exception as error:
        return error_response(
            "CONNECTION_CREATE_FAILED",
            str(error),
            409,
        )

    (
        final_connection,
        test_result,
        test_error,
    ) = test_after_save(
        created_connection
    )

    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "connection":
                        connection_to_json(
                            final_connection
                        ),

                    "test":
                        test_result,

                    "testError":
                        test_error,
                },
            }
        ),
        201,
    )


@integrations_blueprint.put(
    "/<int:connection_id>"
)
@require_auth
def modify_connection(
    connection_id: int,
):
    if not can_manage_integrations():
        return error_response(
            "INSUFFICIENT_PERMISSIONS",
            (
                "Vous ne pouvez pas modifier "
                "une intégration."
            ),
            403,
        )

    existing = find_connection(
        connection_id
    )

    if existing is None:
        return error_response(
            "CONNECTION_NOT_FOUND",
            "Connexion introuvable.",
            404,
        )

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
        configuration = read_payload(
            payload,
            existing,
        )

    except (TypeError, ValueError):
        return error_response(
            "INVALID_CONFIGURATION",
            "La configuration contient "
            "une valeur invalide.",
            400,
        )

    validation_error = (
        validate_configuration(
            configuration,
            existing,
        )
    )

    if validation_error:
        return error_response(
            "INVALID_CONFIGURATION",
            validation_error,
            400,
        )

    replace_secret = bool(
        configuration["credential"]
        or configuration["auth_type"]
        == "none"
    )

    secret_ciphertext = None

    if configuration["auth_type"] == "none":
        replace_secret = True

    elif configuration["credential"]:
        secret_ciphertext = (
            encrypt_credential(
                configuration["credential"]
            )
        )

    try:
        updated_connection = update_connection(
            connection_id=connection_id,

            name=configuration["name"],

            provider_type=configuration[
                "provider_type"
            ],

            base_url=configuration[
                "base_url"
            ],

            environment=configuration[
                "environment"
            ],

            description=configuration[
                "description"
            ],

            auth_type=configuration[
                "auth_type"
            ],

            username=configuration[
                "username"
            ],

            replace_secret=replace_secret,

            secret_ciphertext=
                secret_ciphertext,

            monitoring_enabled=
                configuration[
                    "monitoring_enabled"
                ],

            check_interval_seconds=
                configuration[
                    "check_interval_seconds"
                ],

            failure_threshold=
                configuration[
                    "failure_threshold"
                ],

            user_id=int(
                g.current_user["id"]
            ),
        )

    except Exception as error:
        return error_response(
            "CONNECTION_UPDATE_FAILED",
            str(error),
            409,
        )

    if updated_connection is None:
        return error_response(
            "CONNECTION_NOT_FOUND",
            "Connexion introuvable.",
            404,
        )

    (
        final_connection,
        test_result,
        test_error,
    ) = test_after_save(
        updated_connection
    )

    return jsonify(
        {
            "success": True,
            "data": {
                "connection":
                    connection_to_json(
                        final_connection
                    ),

                "test":
                    test_result,

                "testError":
                    test_error,
            },
        }
    )


@integrations_blueprint.delete(
    "/<int:connection_id>"
)
@require_auth
def remove_connection(
    connection_id: int,
):
    if not can_manage_integrations():
        return error_response(
            "INSUFFICIENT_PERMISSIONS",
            (
                "Vous ne pouvez pas supprimer "
                "une intégration."
            ),
            403,
        )

    result = delete_connection(
        connection_id=connection_id
    )

    if result["reason"] == "not_found":
        return error_response(
            "CONNECTION_NOT_FOUND",
            "Connexion introuvable.",
            404,
        )

    if result["reason"] == "in_use":
        return error_response(
            "CONNECTION_IN_USE",
            (
                f"La connexion « {result['name']} » "
                f"est utilisée par "
                f"{result['usageCount']} environnement(s). "
                "Modifiez les environnements concernés "
                "avant de la supprimer."
            ),
            409,
        )

    return jsonify(
        {
            "success": True,
            "data": {
                "deletedConnection": {
                    "id": connection_id,
                    "name": result["name"],
                },
            },
        }
    )


@integrations_blueprint.post(
    "/test"
)
@require_auth
def test_draft_connection():
    """
    Teste le formulaire avant son enregistrement.
    """

    payload = request.get_json(
        silent=True
    )

    if not isinstance(payload, dict):
        return error_response(
            "INVALID_JSON",
            "Le corps JSON est invalide.",
            400,
        )

    connection_id = payload.get(
        "connectionId"
    )

    existing = None

    if connection_id is not None:
        existing = find_connection(
            int(connection_id)
        )

    try:
        configuration = read_payload(
            payload,
            existing,
        )

    except (TypeError, ValueError):
        return error_response(
            "INVALID_CONFIGURATION",
            "La configuration contient "
            "une valeur invalide.",
            400,
        )

    validation_error = (
        validate_configuration(
            configuration,
            existing,
        )
    )

    if validation_error:
        return error_response(
            "INVALID_CONFIGURATION",
            validation_error,
            400,
        )

    credential = configuration[
        "credential"
    ]

    if (
        not credential
        and existing is not None
    ):
        credential = decrypt_credential(
            existing[
                "secret_ciphertext"
            ]
        )

    temporary_configuration = {
        "provider_type":
            configuration[
                "provider_type"
            ],

        "base_url":
            configuration["base_url"],

        "verify_ssl": True,

        "auth_type":
            configuration["auth_type"],

        "username":
            configuration["username"],
    }

    result = test_configuration(
        temporary_configuration,
        credential,
    )

    return jsonify(
        {
            "success": True,
            "data": {
                "test": result,
            },
        }
    )


@integrations_blueprint.post(
    "/<int:connection_id>/test"
)
@require_auth
def test_existing_connection(
    connection_id: int,
):
    if not can_manage_integrations():
        return error_response(
            "INSUFFICIENT_PERMISSIONS",
            (
                "Vous ne pouvez pas tester "
                "une intégration."
            ),
            403,
        )

    try:
        (
            connection,
            test_result,
        ) = test_saved_connection(
            connection_id
        )

    except ValueError as error:
        return error_response(
            "CONNECTION_NOT_FOUND",
            str(error),
            404,
        )

    except Exception as error:
        return error_response(
            "CONNECTION_TEST_FAILED",
            str(error),
            500,
        )

    return jsonify(
        {
            "success": True,
            "data": {
                "connection":
                    connection_to_json(
                        connection
                    ),

                "test": test_result,
            },
        }
    )