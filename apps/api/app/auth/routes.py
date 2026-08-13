from __future__ import annotations

import re
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from typing import Any

from flask import (
    Blueprint,
    current_app,
    g,
    jsonify,
    request,
)

from app.auth.decorators import require_auth
from app.auth.repository import (
    create_audit_log,
    create_auth_session,
    create_pending_user,
    find_user_by_identifier,
    find_user_by_session,
    get_user_password_hash,
    record_login_attempt,
    revoke_auth_session,
    revoke_other_auth_sessions,
    update_last_login,
    update_user_password,
    update_user_profile,
)
from app.auth.security import (
    generate_session_token,
    hash_password,
    hash_session_token,
    verify_password,
)


auth_blueprint = Blueprint(
    "auth",
    __name__,
)

USERNAME_PATTERN = re.compile(
    r"^[A-Za-z0-9._-]{3,60}$"
)


ACCOUNT_STATUS_MESSAGES = {
    "pending": (
        "ACCOUNT_PENDING",
        "Votre compte est en attente de validation par un administrateur.",
    ),
    "rejected": (
        "ACCOUNT_REJECTED",
        "Votre demande d'accès a été refusée.",
    ),
    "suspended": (
        "ACCOUNT_SUSPENDED",
        "Votre compte est suspendu. Contactez un administrateur.",
    ),
}


def user_to_json(
    user: dict[str, Any],
) -> dict[str, Any]:
    """Retourne uniquement les informations autorisées au frontend."""

    return {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "firstName": user.get("first_name"),
        "lastName": user.get("last_name"),
        "company": user.get("company"),
        "status": user.get("status", "active"),
        "lastLoginAt": user.get("last_login_at"),
        "createdAt": user.get("created_at"),
        "roles": list(
            user.get("roles") or []
        ),
    }


def error_response(
    code: str,
    message: str,
    status: int,
):
    """Forme commune des erreurs de l'API."""

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


def request_ip_address() -> str | None:
    """
    Utilise l'adresse vue par Flask. Si l'application passe derrière un
    reverse proxy, configurez ProxyFix côté serveur avant d'exploiter
    X-Forwarded-For comme source fiable.
    """

    return request.remote_addr


def request_user_agent() -> str | None:
    value = request.headers.get("User-Agent", "").strip()
    return value or None


def optional_text(
    payload: dict[str, Any],
    key: str,
    maximum_length: int,
) -> str | None:
    value = str(payload.get(key, "")).strip()

    if not value:
        return None

    return value[:maximum_length]


@auth_blueprint.post("/register")
def register():
    """
    POST /api/auth/register

    Crée un compte en attente. Aucun rôle n'est attribué avant
    l'approbation d'un administrateur.
    """

    payload = request.get_json(silent=True)

    if not isinstance(payload, dict):
        return error_response(
            "INVALID_JSON",
            "Le corps JSON est invalide.",
            400,
        )

    username = str(payload.get("username", "")).strip()
    email = str(payload.get("email", "")).strip().lower()
    password = str(payload.get("password", ""))
    first_name = optional_text(payload, "firstName", 100)
    last_name = optional_text(payload, "lastName", 100)
    company = optional_text(payload, "company", 180)

    if not USERNAME_PATTERN.fullmatch(username):
        return error_response(
            "INVALID_USERNAME",
            (
                "Le nom d'utilisateur doit contenir entre 3 et 60 "
                "caractères et utiliser uniquement lettres, chiffres, "
                "points, tirets ou underscores."
            ),
            400,
        )

    if len(email) > 255 or "@" not in email or email.startswith("@"):
        return error_response(
            "INVALID_EMAIL",
            "L'adresse email est invalide.",
            400,
        )

    if len(password) < 9:
        return error_response(
            "WEAK_PASSWORD",
            "Le mot de passe doit contenir au moins 9 caractères.",
            400,
        )

    try:
        user = create_pending_user(
            username=username,
            email=email,
            password_hash=hash_password(password),
            first_name=first_name,
            last_name=last_name,
            company=company,
        )
    except ValueError as error:
        return error_response(
            "ACCOUNT_ALREADY_EXISTS",
            str(error),
            409,
        )

    create_audit_log(
        actor_user_id=int(user["id"]),
        action="USER_REGISTERED",
        resource_type="user",
        resource_id=user["id"],
        metadata={
            "status": "pending",
        },
    )

    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "message": (
                        "Votre inscription a été enregistrée. "
                        "Un administrateur doit maintenant valider votre compte."
                    ),
                    "user": {
                        "id": user["id"],
                        "username": user["username"],
                        "email": user["email"],
                        "status": user["status"],
                        "createdAt": user["created_at"],
                    },
                },
            }
        ),
        201,
    )


@auth_blueprint.post("/login")
def login():
    """POST /api/auth/login — vérifie le mot de passe et crée une session."""

    payload = request.get_json(silent=True)

    if not isinstance(payload, dict):
        return error_response(
            "INVALID_JSON",
            "Le corps JSON est invalide.",
            400,
        )

    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    remember_me = payload.get("rememberMe", False)

    if not username or not password:
        return error_response(
            "MISSING_CREDENTIALS",
            "Le nom d'utilisateur et le mot de passe sont obligatoires.",
            400,
        )

    if not isinstance(remember_me, bool):
        return error_response(
            "INVALID_REMEMBER_ME",
            "rememberMe doit être un booléen.",
            400,
        )

    user = find_user_by_identifier(username)

    if user is None:
        record_login_attempt(
            user_id=None,
            identifier=username,
            success=False,
            failure_reason="INVALID_CREDENTIALS",
            ip_address=request_ip_address(),
            user_agent=request_user_agent(),
        )
        return error_response(
            "INVALID_CREDENTIALS",
            "Nom d'utilisateur ou mot de passe incorrect.",
            401,
        )

    password_is_valid = verify_password(
        user["password_hash"],
        password,
    )

    if not password_is_valid:
        record_login_attempt(
            user_id=int(user["id"]),
            identifier=username,
            success=False,
            failure_reason="INVALID_CREDENTIALS",
            ip_address=request_ip_address(),
            user_agent=request_user_agent(),
        )
        return error_response(
            "INVALID_CREDENTIALS",
            "Nom d'utilisateur ou mot de passe incorrect.",
            401,
        )

    account_status = str(user.get("status") or "active")

    if account_status in ACCOUNT_STATUS_MESSAGES:
        code, message = ACCOUNT_STATUS_MESSAGES[account_status]
        record_login_attempt(
            user_id=int(user["id"]),
            identifier=username,
            success=False,
            failure_reason=code,
            ip_address=request_ip_address(),
            user_agent=request_user_agent(),
        )
        return error_response(code, message, 403)

    if not user["is_active"] or account_status != "active":
        record_login_attempt(
            user_id=int(user["id"]),
            identifier=username,
            success=False,
            failure_reason="ACCOUNT_DISABLED",
            ip_address=request_ip_address(),
            user_agent=request_user_agent(),
        )
        return error_response(
            "ACCOUNT_DISABLED",
            "Ce compte utilisateur est désactivé.",
            403,
        )

    now = datetime.now(timezone.utc)

    if remember_me:
        expires_at = now + timedelta(
            days=current_app.config["AUTH_REMEMBER_DAYS"]
        )
    else:
        expires_at = now + timedelta(
            hours=current_app.config["AUTH_SESSION_HOURS"]
        )

    session_token = generate_session_token()
    token_hash = hash_session_token(session_token)

    create_auth_session(
        user_id=user["id"],
        token_hash=token_hash,
        remember_me=remember_me,
        expires_at=expires_at,
    )

    update_last_login(user["id"])
    record_login_attempt(
        user_id=int(user["id"]),
        identifier=username,
        success=True,
        failure_reason=None,
        ip_address=request_ip_address(),
        user_agent=request_user_agent(),
    )

    user["last_login_at"] = now

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
    token = read_bearer_token()

    if token is None:
        return error_response(
            "AUTHENTICATION_REQUIRED",
            "Une authentification est requise.",
            401,
        )

    user = find_user_by_session(
        hash_session_token(token)
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


@auth_blueprint.put("/profile")
@require_auth
def update_profile():
    payload = request.get_json(silent=True)

    if not isinstance(payload, dict):
        return error_response(
            "INVALID_JSON",
            "Le corps JSON est invalide.",
            400,
        )

    email = str(payload.get("email", "")).strip().lower()

    if len(email) > 255 or "@" not in email or email.startswith("@"):
        return error_response(
            "INVALID_EMAIL",
            "L'adresse email est invalide.",
            400,
        )

    try:
        user = update_user_profile(
            user_id=int(g.current_user["id"]),
            email=email,
            first_name=optional_text(payload, "firstName", 100),
            last_name=optional_text(payload, "lastName", 100),
            company=optional_text(payload, "company", 180),
        )
    except ValueError as error:
        return error_response(
            "EMAIL_ALREADY_USED",
            str(error),
            409,
        )

    create_audit_log(
        actor_user_id=int(g.current_user["id"]),
        action="USER_PROFILE_UPDATED",
        resource_type="user",
        resource_id=g.current_user["id"],
    )

    return jsonify(
        {
            "success": True,
            "data": {
                "user": user_to_json(user),
                "message": "Votre profil a été mis à jour.",
            },
        }
    )


@auth_blueprint.post("/change-password")
@require_auth
def change_password():
    payload = request.get_json(silent=True)

    if not isinstance(payload, dict):
        return error_response(
            "INVALID_JSON",
            "Le corps JSON est invalide.",
            400,
        )

    current_password = str(payload.get("currentPassword", ""))
    new_password = str(payload.get("newPassword", ""))

    if not current_password or not new_password:
        return error_response(
            "MISSING_PASSWORD",
            "Le mot de passe actuel et le nouveau mot de passe sont obligatoires.",
            400,
        )

    if len(new_password) < 9:
        return error_response(
            "WEAK_PASSWORD",
            "Le nouveau mot de passe doit contenir au moins 9 caractères.",
            400,
        )

    user_id = int(g.current_user["id"])
    current_hash = get_user_password_hash(user_id)

    if current_hash is None or not verify_password(current_hash, current_password):
        return error_response(
            "INVALID_CURRENT_PASSWORD",
            "Le mot de passe actuel est incorrect.",
            400,
        )

    if verify_password(current_hash, new_password):
        return error_response(
            "PASSWORD_UNCHANGED",
            "Le nouveau mot de passe doit être différent de l'ancien.",
            400,
        )

    update_user_password(
        user_id=user_id,
        password_hash=hash_password(new_password),
    )

    revoked_sessions = revoke_other_auth_sessions(
        user_id=user_id,
        current_session_id=int(g.current_session_id),
    )

    create_audit_log(
        actor_user_id=user_id,
        action="USER_PASSWORD_CHANGED",
        resource_type="user",
        resource_id=user_id,
        metadata={
            "revokedOtherSessions": revoked_sessions,
        },
    )

    return jsonify(
        {
            "success": True,
            "data": {
                "message": (
                    "Mot de passe modifié. Les autres sessions ont été révoquées."
                ),
                "revokedSessions": revoked_sessions,
            },
        }
    )


@auth_blueprint.post("/sessions/revoke-others")
@require_auth
def revoke_other_sessions():
    user_id = int(g.current_user["id"])
    revoked_sessions = revoke_other_auth_sessions(
        user_id=user_id,
        current_session_id=int(g.current_session_id),
    )

    create_audit_log(
        actor_user_id=user_id,
        action="USER_SESSIONS_REVOKED",
        resource_type="user",
        resource_id=user_id,
        metadata={
            "revokedSessions": revoked_sessions,
        },
    )

    return jsonify(
        {
            "success": True,
            "data": {
                "message": "Les autres sessions ont été déconnectées.",
                "revokedSessions": revoked_sessions,
            },
        }
    )


@auth_blueprint.post("/logout")
def logout():
    token = read_bearer_token()

    if token is None:
        return error_response(
            "AUTHENTICATION_REQUIRED",
            "Une authentification est requise.",
            401,
        )

    user = find_user_by_session(
        hash_session_token(token)
    )

    if user is None:
        return error_response(
            "INVALID_SESSION",
            "La session est invalide ou expirée.",
            401,
        )

    revoke_auth_session(user["session_id"])

    return jsonify(
        {
            "success": True,
            "data": {
                "message": "Déconnexion réussie.",
            },
        }
    )
