from __future__ import annotations

import hashlib
import secrets

from werkzeug.security import (
    check_password_hash,
    generate_password_hash,
)


def hash_password(password: str) -> str:
    """
    Transforme un mot de passe en hash sécurisé.

    Le résultat peut être enregistré dans PostgreSQL.
    """

    return generate_password_hash(password)


def verify_password(
    password_hash: str,
    password: str,
) -> bool:
    """
    Vérifie si le mot de passe saisi correspond
    au hash enregistré dans PostgreSQL.
    """

    return check_password_hash(
        password_hash,
        password,
    )


def generate_session_token() -> str:
    """
    Crée un token de session aléatoire.

    Ce token sera retourné à Angular.
    """

    return secrets.token_urlsafe(48)


def hash_session_token(token: str) -> str:
    """
    Calcule le hash SHA-256 du token.

    PostgreSQL conserve uniquement ce hash,
    jamais le token original.
    """

    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()