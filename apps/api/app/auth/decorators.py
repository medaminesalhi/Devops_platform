from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar, cast

from flask import g, jsonify, request

from app.auth.repository import find_user_by_session
from app.auth.security import hash_session_token
from app.database import get_database_connection


RouteFunction = TypeVar(
    "RouteFunction",
    bound=Callable[..., Any],
)


ADMIN_ROLES = {
    "admin",
    "administrator",
}


def read_bearer_token() -> str | None:
    """Lit le token Authorization: Bearer TOKEN."""

    authorization = request.headers.get(
        "Authorization",
        "",
    ).strip()

    if not authorization:
        return None

    parts = authorization.split(
        " ",
        maxsplit=1,
    )

    if len(parts) != 2:
        return None

    scheme, token = parts

    if scheme.lower() != "bearer":
        return None

    return token.strip() or None


def authentication_error():
    return (
        jsonify(
            {
                "success": False,
                "error": {
                    "code": "AUTHENTICATION_REQUIRED",
                    "message": "Une authentification est requise.",
                },
            }
        ),
        401,
    )


def invalid_session_error():
    return (
        jsonify(
            {
                "success": False,
                "error": {
                    "code": "INVALID_SESSION",
                    "message": "La session est invalide ou expirée.",
                },
            }
        ),
        401,
    )


def resource_not_found_error(
    *,
    code: str,
    message: str,
):
    """
    Retourne volontairement 404 pour une ressource appartenant à un autre
    utilisateur. Cela évite de révéler qu'un identifiant existe dans le
    compte d'un autre utilisateur.
    """

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
        404,
    )


def current_user_roles() -> set[str]:
    user = getattr(g, "current_user", None) or {}

    return {
        str(role).strip().lower()
        for role in (user.get("roles") or [])
        if str(role).strip()
    }


def current_user_is_admin() -> bool:
    return bool(
        current_user_roles().intersection(ADMIN_ROLES)
    )


def current_user_id() -> int | None:
    user = getattr(g, "current_user", None) or {}
    value = user.get("id")

    if value is None:
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def current_user_can_access_project(
    project_id: int,
) -> bool:
    """
    Un administrateur peut accéder à tous les projets.
    Tout autre utilisateur ne peut accéder qu'aux projets dont
    projects.created_by correspond à son identifiant.

    Les anciens projets sans created_by restent accessibles uniquement
    aux administrateurs.
    """

    user_id = current_user_id()

    if user_id is None:
        return False

    with get_database_connection() as connection:
        row = connection.execute(
            """
                SELECT
                    id,
                    created_by
                FROM projects
                WHERE id = %s
                LIMIT 1;
            """,
            (project_id,),
        ).fetchone()

    if row is None:
        return False

    if current_user_is_admin():
        return True

    owner_id = row.get("created_by")

    return (
        owner_id is not None
        and int(owner_id) == user_id
    )


def current_user_can_access_deployment(
    deployment_id: int,
) -> bool:
    """Applique le même cloisonnement via le projet du déploiement."""

    user_id = current_user_id()

    if user_id is None:
        return False

    with get_database_connection() as connection:
        row = connection.execute(
            """
                SELECT
                    deployment.id,
                    project.created_by
                FROM deployments AS deployment
                INNER JOIN projects AS project
                    ON project.id = deployment.project_id
                WHERE deployment.id = %s
                LIMIT 1;
            """,
            (deployment_id,),
        ).fetchone()

    if row is None:
        return False

    if current_user_is_admin():
        return True

    owner_id = row.get("created_by")

    return (
        owner_id is not None
        and int(owner_id) == user_id
    )


def require_auth(
    route_function: RouteFunction,
) -> RouteFunction:
    """
    Protège une route Flask avec le token enregistré dans PostgreSQL.

    find_user_by_session vérifie également que le compte est encore
    actif et approuvé. Une suspension invalide donc immédiatement les
    anciennes sessions.
    """

    @wraps(route_function)
    def wrapper(
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        token = read_bearer_token()

        if token is None:
            return authentication_error()

        token_hash = hash_session_token(token)
        user = find_user_by_session(token_hash)

        if user is None:
            return invalid_session_error()

        g.current_user = user
        g.current_session_id = user["session_id"]

        return route_function(
            *args,
            **kwargs,
        )

    return cast(
        RouteFunction,
        wrapper,
    )


def require_roles(
    *allowed_roles: str,
) -> Callable[[RouteFunction], RouteFunction]:
    """Protège une route par authentification puis par rôle."""

    normalized_roles = {
        role.strip().lower()
        for role in allowed_roles
        if role.strip()
    }

    def decorator(
        route_function: RouteFunction,
    ) -> RouteFunction:
        @require_auth
        @wraps(route_function)
        def wrapper(
            *args: Any,
            **kwargs: Any,
        ) -> Any:
            if not current_user_roles().intersection(normalized_roles):
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": {
                                "code": "FORBIDDEN",
                                "message": (
                                    "Vous n'avez pas les droits nécessaires "
                                    "pour effectuer cette action."
                                ),
                            },
                        }
                    ),
                    403,
                )

            return route_function(
                *args,
                **kwargs,
            )

        return cast(
            RouteFunction,
            wrapper,
        )

    return decorator


def require_project_access(
    route_function: RouteFunction,
) -> RouteFunction:
    """
    À placer après @require_auth sur une route contenant project_id.

    Exemple :
        @require_auth
        @require_project_access
        def route(project_id: int): ...
    """

    @wraps(route_function)
    def wrapper(
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        project_id = kwargs.get("project_id")

        if project_id is None:
            raise RuntimeError(
                "require_project_access nécessite un paramètre project_id."
            )

        if not current_user_can_access_project(int(project_id)):
            return resource_not_found_error(
                code="PROJECT_NOT_FOUND",
                message="Le projet est introuvable.",
            )

        return route_function(
            *args,
            **kwargs,
        )

    return cast(
        RouteFunction,
        wrapper,
    )


def require_deployment_access(
    route_function: RouteFunction,
) -> RouteFunction:
    """
    À placer après @require_auth sur une route contenant deployment_id.
    """

    @wraps(route_function)
    def wrapper(
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        deployment_id = kwargs.get("deployment_id")

        if deployment_id is None:
            raise RuntimeError(
                "require_deployment_access nécessite un paramètre deployment_id."
            )

        if not current_user_can_access_deployment(int(deployment_id)):
            return resource_not_found_error(
                code="DEPLOYMENT_NOT_FOUND",
                message="Le déploiement est introuvable.",
            )

        return route_function(
            *args,
            **kwargs,
        )

    return cast(
        RouteFunction,
        wrapper,
    )
