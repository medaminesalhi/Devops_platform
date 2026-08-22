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

DEFAULT_DEPLOYMENT_WORKSPACE_ROOT = (
    API_ROOT
    / "var"
    / "deployments"
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


def read_boolean(
    variable_name: str,
    default_value: bool,
) -> bool:
    raw_value = os.getenv(variable_name)
    if raw_value is None:
        return default_value

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    raise RuntimeError(
        f"{variable_name} doit contenir true/false."
    )


def read_csv_strings(
    variable_name: str,
    default_value: tuple[str, ...] = (),
) -> tuple[str, ...]:
    raw_value = os.getenv(variable_name)
    if raw_value is None:
        return default_value
    return tuple(
        item.strip()
        for item in raw_value.split(",")
        if item.strip()
    )


def read_csv_integers(
    variable_name: str,
    default_value: tuple[int, ...] = (),
) -> tuple[int, ...]:
    values = read_csv_strings(variable_name)
    if not values:
        return default_value

    try:
        return tuple(int(value) for value in values)
    except ValueError as error:
        raise RuntimeError(
            f"{variable_name} doit contenir une liste d'entiers séparés par des virgules."
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
            180,
        )
    )


    AI_OLLAMA_NUM_PREDICT = (
        read_integer(
            "AI_OLLAMA_NUM_PREDICT",
            4096,
        )
    )


    AI_OLLAMA_KEEP_ALIVE = os.getenv(
        "AI_OLLAMA_KEEP_ALIVE",
        "30m",
    )


    AI_OLLAMA_NUM_CTX = (
        read_integer(
            "AI_OLLAMA_NUM_CTX",
            16_384,
        )
    )


    AI_MAX_SOURCE_CONTEXT_BYTES = (
        read_integer(
            "AI_MAX_SOURCE_CONTEXT_BYTES",
            40_000,
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
    DEPLOYMENT_WORKSPACE_ROOT = os.getenv(
        "DEPLOYMENT_WORKSPACE_ROOT",
        str(DEFAULT_DEPLOYMENT_WORKSPACE_ROOT),
    )

    DEPLOYMENT_COMMAND_TIMEOUT_SECONDS = read_integer(
        "DEPLOYMENT_COMMAND_TIMEOUT_SECONDS",
        1800,
    )

    DEPLOYMENT_HTTP_TIMEOUT_SECONDS = read_integer(
        "DEPLOYMENT_HTTP_TIMEOUT_SECONDS",
        30,
    )

    DEPLOYMENT_HEALTH_TIMEOUT_SECONDS = read_integer(
        "DEPLOYMENT_HEALTH_TIMEOUT_SECONDS",
        600,
    )

    DEPLOYMENT_HEALTH_POLL_SECONDS = read_integer(
        "DEPLOYMENT_HEALTH_POLL_SECONDS",
        5,
    )

    DEPLOYMENT_AI_TIMEOUT_SECONDS = read_integer(
        "DEPLOYMENT_AI_TIMEOUT_SECONDS",
        180,
    )

    # Optionnel. Si vide, le diagnostic reprend d'abord le modèle
    # utilisé par la génération. Pour Ollama, il peut ensuite
    # découvrir un modèle disponible via /api/tags.
    DEPLOYMENT_AI_MODEL = os.getenv(
        "DEPLOYMENT_AI_MODEL",
        "",
    )

    # --------------------------------------------------------
    # Tests de performance k6
    # --------------------------------------------------------
    # Aucun paramètre PERFORMANCE_* n'est requis dans .env.
    # Les URLs de cible / Prometheus / Grafana sont fournies
    # par l'utilisateur dans l'interface et stockées par run.
    PERFORMANCE_WORKSPACE_ROOT = str(API_ROOT / "var" / "performance")
    PERFORMANCE_K6_BINARY = "k6"

    # Garde-fous serveur non modifiables par le navigateur.
    PERFORMANCE_MAX_VUS = 500
    PERFORMANCE_MAX_DURATION_SECONDS = 3600
    PERFORMANCE_RUN_GRACE_SECONDS = 120
    PERFORMANCE_STALE_RUN_SECONDS = 300
    PERFORMANCE_MAX_RUNTIME_LOG_LINES = 300
    PERFORMANCE_MAX_RETENTION_DAYS = 90
