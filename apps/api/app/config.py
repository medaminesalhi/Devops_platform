from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


ENV_FILE = (
    Path(__file__).resolve().parent.parent
    / ".env"
)

load_dotenv(ENV_FILE)


def read_integer(
    variable_name: str,
    default_value: int,
) -> int:
    """
    Lit une variable d'environnement entière.
    """

    raw_value = os.getenv(variable_name)

    if raw_value is None:
        return default_value

    try:
        return int(raw_value)

    except ValueError as error:
        raise RuntimeError(
            f"{variable_name} doit contenir un entier."
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

    AUTH_SESSION_HOURS = read_integer(
        "AUTH_SESSION_HOURS",
        8,
    )

    AUTH_REMEMBER_DAYS = read_integer(
        "AUTH_REMEMBER_DAYS",
        7,
    )

    CREDENTIAL_ENCRYPTION_KEY = os.getenv(
        "CREDENTIAL_ENCRYPTION_KEY",
        "",
    )

    INTEGRATION_TIMEOUT_SECONDS = read_integer(
        "INTEGRATION_TIMEOUT_SECONDS",
        10,
    )

    INTEGRATION_WORKER_SLEEP_SECONDS = read_integer(
        "INTEGRATION_WORKER_SLEEP_SECONDS",
        60,
    )