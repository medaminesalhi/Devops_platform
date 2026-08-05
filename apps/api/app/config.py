from __future__ import annotations

import os

from pathlib import Path

from dotenv import load_dotenv


API_ROOT = (
    Path(__file__)
    .resolve()
    .parent
    .parent
)


ENV_FILE = (
    API_ROOT
    / ".env"
)


DEFAULT_PROJECT_ARCHIVE_ROOT = (
    API_ROOT
    / "var"
    / "project-archives"
)


load_dotenv(
    ENV_FILE
)


def read_integer(
    variable_name: str,
    default_value: int,
) -> int:
    """
    Lit une variable d'environnement entière.
    """

    raw_value = os.getenv(
        variable_name
    )

    if raw_value is None:
        return default_value

    try:
        return int(
            raw_value
        )

    except ValueError as error:
        raise RuntimeError(
            (
                f"{variable_name} "
                "doit contenir un entier."
            )
        ) from error


def read_float(
    variable_name: str,
    default_value: float,
) -> float:
    """
    Lit une variable d'environnement décimale.
    """

    raw_value = os.getenv(
        variable_name
    )

    if raw_value is None:
        return default_value

    try:
        return float(
            raw_value
        )

    except ValueError as error:
        raise RuntimeError(
            (
                f"{variable_name} "
                "doit contenir un nombre."
            )
        ) from error


class Config:
    APP_ENV = os.getenv(
        "APP_ENV",
        "development",
    )


    SECRET_KEY = os.getenv(
        "SECRET_KEY",
        "",
    )


    DATABASE_URL = os.getenv(
        "DATABASE_URL",
        "",
    )


    AUTH_SESSION_HOURS = (
        read_integer(
            "AUTH_SESSION_HOURS",
            8,
        )
    )


    AUTH_REMEMBER_DAYS = (
        read_integer(
            "AUTH_REMEMBER_DAYS",
            7,
        )
    )


    CREDENTIAL_ENCRYPTION_KEY = (
        os.getenv(
            "CREDENTIAL_ENCRYPTION_KEY",
            "",
        )
    )


    INTEGRATION_TIMEOUT_SECONDS = (
        read_integer(
            "INTEGRATION_TIMEOUT_SECONDS",
            10,
        )
    )


    INTEGRATION_WORKER_SLEEP_SECONDS = (
        read_integer(
            "INTEGRATION_WORKER_SLEEP_SECONDS",
            60,
        )
    )


    AI_REQUEST_TIMEOUT_SECONDS = (
        read_integer(
            "AI_REQUEST_TIMEOUT_SECONDS",
            120,
        )
    )


    PROJECT_ARCHIVE_ROOT = os.getenv(
        "PROJECT_ARCHIVE_ROOT",
        str(
            DEFAULT_PROJECT_ARCHIVE_ROOT
        ),
    )


    PROJECT_ARCHIVE_MAX_BYTES = (
        read_integer(
            "PROJECT_ARCHIVE_MAX_BYTES",
            100 * 1024 * 1024,
        )
    )


    PROJECT_ARCHIVE_MAX_ENTRIES = (
        read_integer(
            "PROJECT_ARCHIVE_MAX_ENTRIES",
            20_000,
        )
    )


    PROJECT_ARCHIVE_MAX_UNCOMPRESSED_BYTES = (
        read_integer(
            (
                "PROJECT_ARCHIVE_MAX_"
                "UNCOMPRESSED_BYTES"
            ),
            500 * 1024 * 1024,
        )
    )


    PROJECT_ARCHIVE_MAX_COMPRESSION_RATIO = (
        read_float(
            (
                "PROJECT_ARCHIVE_MAX_"
                "COMPRESSION_RATIO"
            ),
            100.0,
        )
    )


    # Marge supplémentaire pour
    # les métadonnées multipart.
    MAX_CONTENT_LENGTH = (
        PROJECT_ARCHIVE_MAX_BYTES
        + 2 * 1024 * 1024
    )