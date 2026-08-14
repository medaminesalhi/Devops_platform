from __future__ import annotations

import re

from typing import Any

from flask import (
    Blueprint,
    g,
    jsonify,
    request,
)

from app.auth.decorators import (
    current_user_id,
    current_user_is_admin,
    require_auth,
    require_environment_access,
)

from app.infrastructure.repository import (
    archive_environment,
    create_environment,
    list_available_connections,
    list_environments,
    update_environment,
)


infrastructure_blueprint = Blueprint(
    "infrastructure",
    __name__,
)


ALLOWED_ENVIRONMENT_TYPES = {
    "lab",
    "staging",
    "production",
    "custom",
}


ALLOWED_SERVICE_ROLES = {
    "kubernetes",
    "argocd",
    "container_registry",
    "gitops_repository",
    "storage",
    "ai_provider",
    "custom_http_service",
}


NAMESPACE_PATTERN = re.compile(
    (
        r"^[a-z0-9]"
        r"(?:[-a-z0-9]*[a-z0-9])?$"
    )
)


DOMAIN_PATTERN = re.compile(
    r"^(?:\*\.)?"
    r"[a-zA-Z0-9]"
    r"(?:[a-zA-Z0-9-]{0,61}"
    r"[a-zA-Z0-9])?"
    r"(?:\."
    r"[a-zA-Z0-9]"
    r"(?:[a-zA-Z0-9-]{0,61}"
    r"[a-zA-Z0-9])?"
    r")*$"
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


def current_user_can_manage(
) -> bool:
    roles = set(
        g.current_user.get(
            "roles"
        )
        or []
    )

    return bool(
        roles.intersection(
            {
                "admin",
                "administrator",
                "devops",
            }
        )
    )


def connection_to_json(
    connection: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id":
            connection["id"],

        "name":
            connection["name"],

        "providerType":
            connection[
                "provider_type"
            ],

        "baseUrl":
            connection["base_url"],

        "description":
            connection["description"],

        "status":
            connection["status"],

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
            connection[
                "last_latency_ms"
            ],
    }


def environment_to_json(
    environment: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id":
            environment["id"],

        "name":
            environment["name"],

        "code":
            environment["code"],

        "environmentType":
            environment[
                "environment_type"
            ],

        "description":
            environment["description"],

        "namespace":
            environment["namespace"],

        "domain":
            environment["domain"],

        "configurationStatus":
            environment[
                "configuration_status"
            ],

        "effectiveStatus":
            environment[
                "effective_status"
            ],

        "isDefault":
            environment["is_default"],

        "serviceTotal":
            environment[
                "service_total"
            ],

        "serviceOnline":
            environment[
                "service_online"
            ],

        "projectCount":
            environment[
                "project_count"
            ],

        "kubernetesConnectionName":
            environment[
                "kubernetes_connection_name"
            ],

        "lastCheckedAt": (
            environment[
                "last_checked_at"
            ].isoformat()

            if environment[
                "last_checked_at"
            ]

            else None
        ),

        "services":
            environment["services"]
            or [],

        "createdAt":
            environment[
                "created_at"
            ].isoformat(),

        "updatedAt":
            environment[
                "updated_at"
            ].isoformat(),
    }


def create_code(
    name: str,
) -> str:
    normalized = (
        name.lower().strip()
    )

    normalized = re.sub(
        r"[^a-z0-9]+",
        "-",
        normalized,
    )

    return normalized.strip("-")


def normalize_optional_text(
    value: Any,
) -> str | None:
    normalized = str(
        value or ""
    ).strip()

    return normalized or None


def read_environment_payload(
    payload: dict[str, Any],
) -> tuple[
    dict[str, Any] | None,
    str | None,
]:
    name = str(
        payload.get("name")
        or ""
    ).strip()


    environment_type = str(
        payload.get(
            "environmentType"
        )
        or ""
    ).strip()


    namespace = str(
        payload.get("namespace")
        or ""
    ).strip()


    description = (
        normalize_optional_text(
            payload.get(
                "description"
            )
        )
    )


    domain = normalize_optional_text(
        payload.get("domain")
    )


    if not name:
        return (
            None,

            (
                "Le nom de l'environnement "
                "est obligatoire."
            ),
        )


    if len(name) > 140:
        return (
            None,

            (
                "Le nom ne peut pas dépasser "
                "140 caractères."
            ),
        )


    if (
        environment_type
        not in ALLOWED_ENVIRONMENT_TYPES
    ):
        return (
            None,

            (
                "Le type d'environnement "
                "est invalide."
            ),
        )


    if not namespace:
        return (
            None,

            (
                "Le namespace Kubernetes "
                "est obligatoire."
            ),
        )


    if (
        len(namespace) > 63

        or not NAMESPACE_PATTERN.fullmatch(
            namespace
        )
    ):
        return (
            None,

            (
                "Le namespace doit contenir "
                "uniquement des lettres "
                "minuscules, des chiffres "
                "et des tirets, sur "
                "63 caractères maximum."
            ),
        )


    if (
        domain

        and (
            len(domain) > 255

            or not DOMAIN_PATTERN.fullmatch(
                domain
            )
        )
    ):
        return (
            None,
            "Le domaine saisi est invalide.",
        )


    raw_connections = payload.get(
        "connectionIds",
        {},
    )


    if not isinstance(
        raw_connections,
        dict,
    ):
        return (
            None,

            (
                "La liste des connexions "
                "est invalide."
            ),
        )


    connection_ids: dict[
        str,
        int,
    ] = {}


    for (
        service_role,
        raw_connection_id,
    ) in raw_connections.items():
        if (
            service_role
            not in ALLOWED_SERVICE_ROLES
        ):
            continue


        if raw_connection_id in {
            None,
            "",
            0,
            "0",
        }:
            continue


        try:
            connection_ids[
                service_role
            ] = int(
                raw_connection_id
            )

        except (
            TypeError,
            ValueError,
        ):
            return (
                None,

                (
                    "Une connexion sélectionnée "
                    "est invalide."
                ),
            )


    return (
        {
            "name":
                name,

            "code":
                create_code(name),

            "environment_type":
                environment_type,

            "description":
                description,

            "namespace":
                namespace,

            "domain":
                domain,

            "connection_ids":
                connection_ids,
        },

        None,
    )


# ============================================================
# OVERVIEW
# ============================================================

@infrastructure_blueprint.get(
    "/overview"
)
@require_auth
def get_overview():
    raw_environment_type = (
        request.args.get(
            "environmentType"
        )
    )


    environment_type = (
        raw_environment_type

        if raw_environment_type
        in ALLOWED_ENVIRONMENT_TYPES

        else None
    )


    owner_user_id = (
        None
        if current_user_is_admin()
        else current_user_id()
    )

    connections = (
        list_available_connections(
            owner_user_id=owner_user_id,
        )
    )


    environments = list_environments(
        environment_type=
            environment_type,
        owner_user_id=
            owner_user_id,
    )


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

                "environments": [
                    environment_to_json(
                        environment
                    )

                    for environment
                    in environments
                ],

                "summary": {
                    "total":
                        len(environments),

                    "ready":
                        sum(
                            1

                            for environment
                            in environments

                            if environment[
                                "effective_status"
                            ] == "ready"
                        ),

                    "degraded":
                        sum(
                            1

                            for environment
                            in environments

                            if environment[
                                "effective_status"
                            ] == "degraded"
                        ),

                    "offline":
                        sum(
                            1

                            for environment
                            in environments

                            if environment[
                                "effective_status"
                            ] == "offline"
                        ),

                    "draft":
                        sum(
                            1

                            for environment
                            in environments

                            if environment[
                                "effective_status"
                            ] == "draft"
                        ),
                },
            },
        }
    )


# ============================================================
# CRÉATION
# ============================================================

@infrastructure_blueprint.post(
    "/environments"
)
@require_auth
def create_new_environment():
    if not current_user_can_manage():
        return error_response(
            "INSUFFICIENT_PERMISSIONS",

            (
                "Vous ne pouvez pas créer "
                "un environnement."
            ),

            403,
        )


    payload = request.get_json(
        silent=True
    )


    if not isinstance(
        payload,
        dict,
    ):
        return error_response(
            "INVALID_JSON",
            "Le corps JSON est invalide.",
            400,
        )


    (
        configuration,
        validation_error,
    ) = read_environment_payload(
        payload
    )


    if validation_error:
        return error_response(
            "INVALID_ENVIRONMENT",
            validation_error,
            400,
        )


    assert configuration is not None


    try:
        environment = create_environment(
            **configuration,

            user_id=int(
                g.current_user["id"]
            ),
            connection_owner_user_id=(
                None
                if current_user_is_admin()
                else current_user_id()
            ),
        )

    except ValueError as error:
        return error_response(
            "INVALID_ENVIRONMENT",
            str(error),
            400,
        )

    except Exception:
        return error_response(
            "ENVIRONMENT_CREATE_FAILED",

            (
                "Impossible de créer "
                "l'environnement. Vérifiez "
                "qu'un environnement du même "
                "nom n'existe pas déjà."
            ),

            409,
        )


    return (
        jsonify(
            {
                "success": True,

                "data": {
                    "environment":
                        environment_to_json(
                            environment
                        ),
                },
            }
        ),

        201,
    )


# ============================================================
# MODIFICATION
# ============================================================

@infrastructure_blueprint.put(
    "/environments/"
    "<int:environment_id>"
)
@require_auth
@require_environment_access
def modify_environment(
    environment_id: int,
):
    if not current_user_can_manage():
        return error_response(
            "INSUFFICIENT_PERMISSIONS",

            (
                "Vous ne pouvez pas modifier "
                "un environnement."
            ),

            403,
        )


    payload = request.get_json(
        silent=True
    )


    if not isinstance(
        payload,
        dict,
    ):
        return error_response(
            "INVALID_JSON",
            "Le corps JSON est invalide.",
            400,
        )


    (
        configuration,
        validation_error,
    ) = read_environment_payload(
        payload
    )


    if validation_error:
        return error_response(
            "INVALID_ENVIRONMENT",
            validation_error,
            400,
        )


    assert configuration is not None


    try:
        environment = update_environment(
            environment_id=
                environment_id,

            **configuration,
            connection_owner_user_id=(
                None
                if current_user_is_admin()
                else current_user_id()
            ),
        )

    except ValueError as error:
        return error_response(
            "INVALID_ENVIRONMENT",
            str(error),
            400,
        )

    except Exception:
        return error_response(
            "ENVIRONMENT_UPDATE_FAILED",

            (
                "Impossible de modifier "
                "l'environnement. Vérifiez "
                "les valeurs saisies."
            ),

            409,
        )


    return jsonify(
        {
            "success": True,

            "data": {
                "environment":
                    environment_to_json(
                        environment
                    ),
            },
        }
    )


# ============================================================
# ARCHIVAGE
# ============================================================

@infrastructure_blueprint.delete(
    "/environments/"
    "<int:environment_id>"
)
@require_auth
@require_environment_access
def remove_environment(
    environment_id: int,
):
    if not current_user_can_manage():
        return error_response(
            "INSUFFICIENT_PERMISSIONS",

            (
                "Vous ne pouvez pas archiver "
                "un environnement."
            ),

            403,
        )


    archived = archive_environment(
        environment_id=
            environment_id,
    )


    if archived is None:
        return error_response(
            "ENVIRONMENT_NOT_FOUND",

            (
                "L'environnement "
                "est introuvable."
            ),

            404,
        )


    return jsonify(
        {
            "success": True,

            "data": {
                "archivedEnvironment":
                    archived,
            },
        }
    )