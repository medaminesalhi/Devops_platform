from __future__ import annotations

from cryptography.fernet import (
    Fernet,
    InvalidToken,
)

from flask import current_app


def get_credential_cipher() -> Fernet:
    """
    Crée le système de chiffrement à partir
    de la clé stockée dans le fichier .env.
    """

    encryption_key = current_app.config.get(
        "CREDENTIAL_ENCRYPTION_KEY",
        "",
    ).strip()

    if not encryption_key:
        raise RuntimeError(
            "CREDENTIAL_ENCRYPTION_KEY "
            "n'est pas configurée."
        )

    try:
        return Fernet(
            encryption_key.encode("utf-8")
        )

    except ValueError as error:
        raise RuntimeError(
            "CREDENTIAL_ENCRYPTION_KEY "
            "n'est pas une clé Fernet valide."
        ) from error


def encrypt_credential(
    secret: str,
) -> str:
    """
    Chiffre un token ou mot de passe avant
    son enregistrement dans PostgreSQL.
    """

    encrypted_secret = (
        get_credential_cipher().encrypt(
            secret.encode("utf-8")
        )
    )

    return encrypted_secret.decode("utf-8")


def decrypt_credential(
    secret_ciphertext: str | None,
) -> str | None:
    """
    Déchiffre temporairement un credential.

    Le résultat ne doit jamais être renvoyé
    au frontend Angular.
    """

    if not secret_ciphertext:
        return None

    try:
        decrypted_secret = (
            get_credential_cipher().decrypt(
                secret_ciphertext.encode(
                    "utf-8"
                )
            )
        )

        return decrypted_secret.decode(
            "utf-8"
        )

    except InvalidToken as error:
        raise RuntimeError(
            "Le credential enregistré "
            "ne peut pas être déchiffré."
        ) from error