from __future__ import annotations

import os

from cryptography.fernet import (
    Fernet,
    InvalidToken,
)

from flask import current_app


class CredentialSecurityError(RuntimeError):
    pass


def _get_encryption_key() -> str:
    key = (
        current_app.config.get(
            "CREDENTIAL_ENCRYPTION_KEY"
        )
        or os.getenv(
            "CREDENTIAL_ENCRYPTION_KEY"
        )
        or ""
    ).strip()

    if not key:
        raise CredentialSecurityError(
            "CREDENTIAL_ENCRYPTION_KEY n'est pas configurée."
        )

    return key


def _get_cipher() -> Fernet:
    try:
        return Fernet(
            _get_encryption_key().encode(
                "utf-8"
            )
        )

    except (ValueError, TypeError) as error:
        raise CredentialSecurityError(
            "La clé de chiffrement est invalide."
        ) from error


def encrypt_credential(
    secret: str | None,
) -> str | None:
    if secret is None:
        return None

    normalized = secret.strip()

    if not normalized:
        return None

    encrypted = _get_cipher().encrypt(
        normalized.encode("utf-8")
    )

    return encrypted.decode("utf-8")


def decrypt_credential(
    encrypted_secret: str | None,
) -> str | None:
    if not encrypted_secret:
        return None

    try:
        decrypted = _get_cipher().decrypt(
            encrypted_secret.encode("utf-8")
        )

    except InvalidToken as error:
        raise CredentialSecurityError(
            "Impossible de déchiffrer le credential."
        ) from error

    return decrypted.decode("utf-8")