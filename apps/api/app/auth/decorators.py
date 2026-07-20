from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar, cast

from flask import g, jsonify, request

from app.auth.repository import find_user_by_session
from app.auth.security import hash_session_token


RouteFunction = TypeVar(
    "RouteFunction",
    bound=Callable[..., Any],
)


def read_bearer_token() -> str | None:
    """
    Lit le token depuis :

    Authorization: Bearer TOKEN
    """

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


def require_auth(
    route_function: RouteFunction,
) -> RouteFunction:
    """
    Protège une route Flask avec le token
    enregistré dans PostgreSQL.
    """

    @wraps(route_function)
    def wrapper(
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        token = read_bearer_token()

        if token is None:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": {
                            "code":
                                "AUTHENTICATION_REQUIRED",

                            "message":
                                "Une authentification "
                                "est requise.",
                        },
                    }
                ),
                401,
            )

        token_hash = hash_session_token(token)

        user = find_user_by_session(token_hash)

        if user is None:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": {
                            "code":
                                "INVALID_SESSION",

                            "message":
                                "La session est invalide "
                                "ou expirée.",
                        },
                    }
                ),
                401,
            )

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