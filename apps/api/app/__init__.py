from __future__ import annotations

from flask import Flask, jsonify

from app.auth.routes import auth_blueprint
from app.commands import register_commands
from app.config import Config
from app.dashboard.routes import (
    dashboard_blueprint,
)
from app.database import (
    database_is_available,
)
from app.integrations.commands import (
    register_integration_commands,
)
from app.integrations.routes import (
    integrations_blueprint,
)


def create_app() -> Flask:
    """
    Crée et configure l'application Flask.
    """

    app = Flask(__name__)

    app.config.from_object(Config)

    if not app.config["SECRET_KEY"]:
        raise RuntimeError(
            "SECRET_KEY n'est pas configurée."
        )

    if not app.config["DATABASE_URL"]:
        raise RuntimeError(
            "DATABASE_URL n'est pas configurée."
        )

    if not app.config[
        "CREDENTIAL_ENCRYPTION_KEY"
    ]:
        raise RuntimeError(
            "CREDENTIAL_ENCRYPTION_KEY "
            "n'est pas configurée."
        )

    app.register_blueprint(
        auth_blueprint,
        url_prefix="/api/auth",
    )

    app.register_blueprint(
        dashboard_blueprint,
        url_prefix="/api/dashboard",
    )

    app.register_blueprint(
        integrations_blueprint,
        url_prefix="/api/integrations",
    )

    register_commands(app)

    register_integration_commands(app)

    @app.get("/api/health")
    def health():
        if not database_is_available():
            return (
                jsonify(
                    {
                        "success": False,
                        "error": {
                            "code":
                                "DATABASE_UNAVAILABLE",

                            "message":
                                "PostgreSQL est "
                                "inaccessible.",
                        },
                    }
                ),
                503,
            )

        return jsonify(
            {
                "success": True,
                "data": {
                    "service":
                        "piximind-api",

                    "status":
                        "healthy",

                    "database":
                        "connected",

                    "environment":
                        app.config["APP_ENV"],
                },
            }
        )

    return app