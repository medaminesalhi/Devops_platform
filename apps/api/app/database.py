from __future__ import annotations

import psycopg

from flask import current_app
from psycopg.rows import dict_row


def get_database_connection():
    """
    Ouvre une connexion PostgreSQL.

    dict_row signifie que PostgreSQL retournera
    les résultats sous forme de dictionnaire.

    Exemple :
    row["username"]
    au lieu de :
    row[0]
    """

    database_url = current_app.config[
        "DATABASE_URL"
    ]

    if not database_url:
        raise RuntimeError(
            "DATABASE_URL n'est pas configurée."
        )

    return psycopg.connect(
        database_url,
        row_factory=dict_row,
    )


def database_is_available() -> bool:
    """
    Vérifie simplement que PostgreSQL répond.
    """

    try:
        with get_database_connection() as connection:
            row = connection.execute(
                "SELECT 1 AS status;"
            ).fetchone()

        return bool(
            row and row["status"] == 1
        )

    except Exception:
        current_app.logger.exception(
            "La connexion PostgreSQL a échoué."
        )

        return False