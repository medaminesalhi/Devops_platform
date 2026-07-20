from __future__ import annotations

import time

import click

from flask import Flask

from app.integrations.service import (
    check_due_connections,
)


def register_integration_commands(
    app: Flask,
) -> None:
    """
    Ajoute les commandes de supervision à Flask.
    """

    @app.cli.command(
        "check-integrations-once"
    )
    def check_once() -> None:
        """
        Exécute un seul cycle de contrôle.
        """

        checked_count = (
            check_due_connections()
        )

        click.secho(
            (
                f"{checked_count} connexion(s) "
                "contrôlée(s)."
            ),
            fg="green",
        )


    @app.cli.command(
        "monitor-integrations"
    )
    def monitor_integrations() -> None:
        """
        Lance le worker permanent.

        Le worker se réveille toutes les minutes,
        mais chaque connexion conserve son propre
        intervalle : 5 minutes, 10 minutes, etc.
        """

        sleep_seconds = app.config[
            "INTEGRATION_WORKER_SLEEP_SECONDS"
        ]

        click.secho(
            (
                "Worker de supervision démarré. "
                "Utilisez Ctrl+C pour l'arrêter."
            ),
            fg="green",
        )

        try:
            while True:
                checked_count = (
                    check_due_connections()
                )

                click.echo(
                    (
                        f"Cycle terminé : "
                        f"{checked_count} contrôle(s)."
                    )
                )

                time.sleep(
                    sleep_seconds
                )

        except KeyboardInterrupt:
            click.echo()

            click.secho(
                "Worker arrêté.",
                fg="yellow",
            )