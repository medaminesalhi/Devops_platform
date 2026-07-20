from __future__ import annotations

import re

import click
from flask import Flask

from app.auth.repository import create_user_with_role
from app.auth.security import hash_password


USERNAME_PATTERN = re.compile(
    r"^[A-Za-z0-9._-]{3,60}$"
)


def register_commands(app: Flask) -> None:
    """
    Enregistre les commandes personnalisées Flask.

    Pour le moment, nous ajoutons uniquement
    la commande create-admin.
    """

    @app.cli.command("create-admin")
    @click.option(
        "--username",
        required=True,
        help="Nom d'utilisateur.",
    )
    @click.option(
        "--email",
        required=True,
        help="Adresse email.",
    )
    @click.option(
        "--first-name",
        default="",
        help="Prénom.",
    )
    @click.option(
        "--last-name",
        default="",
        help="Nom.",
    )
    def create_admin(
        username: str,
        email: str,
        first_name: str,
        last_name: str,
    ) -> None:
        """
        Crée un utilisateur avec le rôle admin.
        """

        username = username.strip()
        email = email.strip().lower()
        first_name = first_name.strip()
        last_name = last_name.strip()

        if not USERNAME_PATTERN.fullmatch(username):
            raise click.ClickException(
                "Le nom d'utilisateur doit contenir "
                "entre 3 et 60 caractères et utiliser "
                "uniquement des lettres, chiffres, points, "
                "tirets ou underscores."
            )

        if "@" not in email:
            raise click.ClickException(
                "L'adresse email est invalide."
            )

        password = click.prompt(
            "Mot de passe",
            hide_input=True,
            confirmation_prompt=True,
        )

        # Limite temporaire pour le développement local.
        # Elle devra être augmentée avant la production.
        if len(password) < 9:
            raise click.ClickException(
                "Le mot de passe doit contenir "
                "au moins 9 caractères."
            )

        password_hash = hash_password(password)

        try:
            user = create_user_with_role(
                username=username,
                email=email,
                password_hash=password_hash,
                first_name=first_name or None,
                last_name=last_name or None,
                role_code="admin",
            )

        except ValueError as error:
            raise click.ClickException(
                str(error)
            ) from error

        click.secho(
            (
                "Administrateur créé avec succès : "
                f"{user['username']}"
            ),
            fg="green",
        )