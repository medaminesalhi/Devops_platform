from __future__ import annotations

from datetime import (
    datetime,
    timedelta,
    timezone,
)
from typing import Any

from flask import (
    Blueprint,
    current_app,
    jsonify,
    request,
)

from app.auth.repository import (
    create_auth_session,
    find_user_by_identifier,
    find_user_by_session,
    revoke_auth_session,
    update_last_login,
)
from app.auth.security import (
    generate_session_token,
    hash_session_token,
    verify_password,
)


auth_blueprint = Blueprint(
    "auth",
    __name__,
)


def user_to_json(
    user: dict[str, Any],
) -> dict[str, Any]:
    """
    Retourne uniquement les informations
    autorisées à être envoyées au frontend.

    password_hash n'est jamais retourné.
    """

    return {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "firstName": user.get("first_name"),
        "lastName": user.get("last_name"),
        "roles": list(
            user.get("roles") or []
        ),
    }


def error_response(
    code: str,
    message: str,
    status: int,
):
    """
    Forme commune des erreurs de l'API.
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
        status,
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


@auth_blueprint.post("/login")
def login():
    """
    POST /api/auth/login

    Vérifie le mot de passe et crée une session.
    """

    payload = request.get_json(
        silent=True,
    )

    if not isinstance(payload, dict):
        return error_response(
            "INVALID_JSON",
            "Le corps JSON est invalide.",
            400,
        )

    username = str(
        payload.get("username", "")
    ).strip()

    password = str(
        payload.get("password", "")
    )

    remember_me = payload.get(
        "rememberMe",
        False,
    )

    if not username or not password:
        return error_response(
            "MISSING_CREDENTIALS",
            "Le nom d'utilisateur et le mot "
            "de passe sont obligatoires.",
            400,
        )

    if not isinstance(remember_me, bool):
        return error_response(
            "INVALID_REMEMBER_ME",
            "rememberMe doit être un booléen.",
            400,
        )

    user = find_user_by_identifier(
        username,
    )

    # Nous retournons le même message lorsqu'un utilisateur
    # est inconnu ou lorsque le mot de passe est incorrect.
    # Cela évite de révéler les comptes existants.
    if user is None:
        return error_response(
            "INVALID_CREDENTIALS",
            "Nom d'utilisateur ou mot de passe incorrect.",
            401,
        )

    if not user["is_active"]:
        return error_response(
            "ACCOUNT_DISABLED",
            "Ce compte utilisateur est désactivé.",
            403,
        )

    password_is_valid = verify_password(
        user["password_hash"],
        password,
    )

    if not password_is_valid:
        return error_response(
            "INVALID_CREDENTIALS",
            "Nom d'utilisateur ou mot de passe incorrect.",
            401,
        )

    now = datetime.now(timezone.utc)

    if remember_me:
        expires_at = now + timedelta(
            days=current_app.config[
                "AUTH_REMEMBER_DAYS"
            ]
        )
    else:
        expires_at = now + timedelta(
            hours=current_app.config[
                "AUTH_SESSION_HOURS"
            ]
        )

    # Token réel envoyé à Angular
    session_token = generate_session_token()

    # Hash du token enregistré dans PostgreSQL
    token_hash = hash_session_token(
        session_token
    )

    create_auth_session(
        user_id=user["id"],
        token_hash=token_hash,
        remember_me=remember_me,
        expires_at=expires_at,
    )

    update_last_login(
        user["id"]
    )

    return jsonify(
        {
            "success": True,
            "data": {
                "accessToken": session_token,
                "tokenType": "Bearer",
                "expiresAt": expires_at.isoformat(),
                "user": user_to_json(user),
            },
        }
    )


@auth_blueprint.get("/me")
def current_user():
    """
    GET /api/auth/me

    Vérifie le token et retourne
    l'utilisateur connecté.
    """

    token = read_bearer_token()

    if token is None:
        return error_response(
            "AUTHENTICATION_REQUIRED",
            "Une authentification est requise.",
            401,
        )

    token_hash = hash_session_token(
        token
    )

    user = find_user_by_session(
        token_hash
    )

    if user is None:
        return error_response(
            "INVALID_SESSION",
            "La session est invalide ou expirée.",
            401,
        )

    return jsonify(
        {
            "success": True,
            "data": {
                "user": user_to_json(user),
            },
        }
    )


@auth_blueprint.post("/logout")
def logout():
    """
    POST /api/auth/logout

    Révoque la session dans PostgreSQL.
    """

    token = read_bearer_token()

    if token is None:
        return error_response(
            "AUTHENTICATION_REQUIRED",
            "Une authentification est requise.",
            401,
        )

    token_hash = hash_session_token(
        token
    )

    user = find_user_by_session(
        token_hash
    )

    if user is None:
        return error_response(
            "INVALID_SESSION",
            "La session est invalide ou expirée.",
            401,
        )

    revoke_auth_session(
        user["session_id"]
    )

    return jsonify(
        {
            "success": True,
            "data": {
                "message": "Déconnexion réussie.",
            },
        }
    )