from __future__ import annotations

import re

from typing import Any

from flask import (
    Blueprint,
    g,
    jsonify,
    request,
)

from app.auth.decorators import require_auth

from app.infrastructure.repository import (
    create_environment,
    list_available_connections,
    list_environments,
    list_visible_clients,
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
    "ai_provider",
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


def current_user_is_global_admin() -> bool:
    roles = set(
        g.current_user.get("roles") or []
    )

    return "admin" in roles


def current_user_can_manage() -> bool:
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


def client_to_json(
    client: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": client["id"],
        "name": client["name"],
        "slug": client["slug"],
        "status": client["status"],
    }


def connection_to_json(
    connection: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": connection["id"],
        "name": connection["name"],

        "providerType":
            connection["provider_type"],

        "baseUrl":
            connection["base_url"],

        "status":
            connection["status"],

        "scope":
            connection["scope"],

        "clientId":
            connection["client_id"],

        "clientName":
            connection["client_name"],
    }


def environment_to_json(
    environment: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": environment["id"],

        "clientId":
            environment["client_id"],

        "clientName":
            environment["client_name"],

        "clientSlug":
            environment["client_slug"],

        "name":
            environment["name"],

        "code":
            environment["code"],

        "environmentType":
            environment["environment_type"],

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
            environment["effective_status"],

        "isDefault":
            environment["is_default"],

        "serviceTotal":
            environment["service_total"],

        "serviceOnline":
            environment["service_online"],

        "projectCount":
            environment["project_count"],

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
            environment["services"] or [],

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
    """
    Convertit un nom en code technique.

    Exemple :
    Client A Production
    devient :
    client-a-production
    """

    normalized = name.lower().strip()

    normalized = re.sub(
        r"[^a-z0-9]+",
        "-",
        normalized,
    )

    return normalized.strip("-")


@infrastructure_blueprint.get(
    "/overview"
)
@require_auth
def get_overview():
    raw_client_id = request.args.get(
        "clientId"
    )

    raw_environment_type = request.args.get(
        "environmentType"
    )

    client_id = None

    if raw_client_id:
        try:
            client_id = int(raw_client_id)

        except ValueError:
            return error_response(
                "INVALID_CLIENT_ID",
                "L'identifiant du client est invalide.",
                400,
            )

    environment_type = (
        raw_environment_type
        if raw_environment_type
        in ALLOWED_ENVIRONMENT_TYPES
        else None
    )

    user_id = int(
        g.current_user["id"]
    )

    is_global_admin = (
        current_user_is_global_admin()
    )

    clients = list_visible_clients(
        user_id=user_id,
        is_global_admin=is_global_admin,
    )

    connections = (
        list_available_connections(
            user_id=user_id,
            is_global_admin=is_global_admin,
        )
    )

    environments = list_environments(
        user_id=user_id,
        is_global_admin=is_global_admin,
        client_id=client_id,
        environment_type=environment_type,
    )

    ready_count = sum(
        1
        for environment in environments
        if environment["effective_status"]
        == "ready"
    )

    degraded_count = sum(
        1
        for environment in environments
        if environment["effective_status"]
        == "degraded"
    )

    offline_count = sum(
        1
        for environment in environments
        if environment["effective_status"]
        == "offline"
    )

    draft_count = sum(
        1
        for environment in environments
        if environment["effective_status"]
        == "draft"
    )

    return jsonify(
        {
            "success": True,
            "data": {
                "clients": [
                    client_to_json(client)
                    for client in clients
                ],

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
                        ready_count,

                    "degraded":
                        degraded_count,

                    "offline":
                        offline_count,

                    "draft":
                        draft_count,
                },
            },
        }
    )


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

    if not isinstance(payload, dict):
        return error_response(
            "INVALID_JSON",
            "Le corps JSON est invalide.",
            400,
        )

    try:
        client_id = int(
            payload.get("clientId")
        )

    except (TypeError, ValueError):
        return error_response(
            "INVALID_CLIENT_ID",
            "Le client est obligatoire.",
            400,
        )

    name = str(
        payload.get("name") or ""
    ).strip()

    environment_type = str(
        payload.get("environmentType")
        or ""
    ).strip()

    namespace = str(
        payload.get("namespace") or ""
    ).strip()

    raw_description = payload.get(
        "description"
    )

    description = (
        str(raw_description).strip()
        if raw_description
        else None
    )

    raw_domain = payload.get("domain")

    domain = (
        str(raw_domain).strip()
        if raw_domain
        else None
    )

    if not name:
        return error_response(
            "NAME_REQUIRED",
            (
                "Le nom de l'environnement "
                "est obligatoire."
            ),
            400,
        )

    if (
        environment_type
        not in ALLOWED_ENVIRONMENT_TYPES
    ):
        return error_response(
            "INVALID_ENVIRONMENT_TYPE",
            (
                "Le type d'environnement "
                "est invalide."
            ),
            400,
        )

    if not namespace:
        return error_response(
            "NAMESPACE_REQUIRED",
            (
                "Le namespace Kubernetes "
                "est obligatoire."
            ),
            400,
        )

    raw_connections = payload.get(
        "connectionIds",
        {},
    )

    if not isinstance(
        raw_connections,
        dict,
    ):
        return error_response(
            "INVALID_CONNECTIONS",
            (
                "La liste des connexions "
                "est invalide."
            ),
            400,
        )

    connection_ids: dict[str, int] = {}

    for service_role, raw_connection_id in (
        raw_connections.items()
    ):
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
            connection_ids[service_role] = (
                int(raw_connection_id)
            )

        except (TypeError, ValueError):
            return error_response(
                "INVALID_CONNECTION_ID",
                (
                    "Une connexion sélectionnée "
                    "est invalide."
                ),
                400,
            )

    try:
        environment = create_environment(
            client_id=client_id,
            name=name,
            code=create_code(name),
            environment_type=
                environment_type,
            description=description,
            namespace=namespace,
            domain=domain,
            connection_ids=connection_ids,
            user_id=int(
                g.current_user["id"]
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