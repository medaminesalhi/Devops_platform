from __future__ import annotations

from pathlib import Path

from flask import (
    Flask,
    jsonify,
)

from werkzeug.exceptions import (
    RequestEntityTooLarge,
)

from app.analysis import (
    analysis_blueprint,
)

from app.admin import (
    admin_blueprint,
)

from app.auth.routes import (
    auth_blueprint,
)

from app.commands import (
    register_commands,
)

from app.config import (
    Config,
)

from app.dashboard.routes import (
    dashboard_blueprint,
)

from app.deployments import (
    deployments_blueprint,
    register_deployment_commands,
)


from app.database import (
    database_is_available,
)

from app.generation import (
    generation_blueprint,
)

from app.infrastructure.routes import (
    infrastructure_blueprint,
)

from app.integrations.commands import (
    register_integration_commands,
)

from app.integrations.routes import (
    integrations_blueprint,
)

from app.notifications import (
    notifications_blueprint,
)

from app.projects.routes import (
    projects_blueprint,
)

from app.workflow import (
    register_workflow_commands,
    workflow_blueprint,
)


def create_app() -> Flask:
    app = Flask(
        __name__
    )


    app.config.from_object(
        Config
    )


    if not app.config[
        "SECRET_KEY"
    ]:
        raise RuntimeError(
            (
                "SECRET_KEY "
                "n'est pas configurée."
            )
        )


    if not app.config[
        "DATABASE_URL"
    ]:
        raise RuntimeError(
            (
                "DATABASE_URL "
                "n'est pas configurée."
            )
        )


    if not app.config[
        "CREDENTIAL_ENCRYPTION_KEY"
    ]:
        raise RuntimeError(
            (
                "CREDENTIAL_ENCRYPTION_KEY "
                "n'est pas configurée."
            )
        )


    archive_root_value = (
        app.config.get(
            "PROJECT_ARCHIVE_ROOT"
        )
    )


    if archive_root_value:
        Path(
            archive_root_value
        ).mkdir(
            parents=True,
            exist_ok=True,
        )


    app.register_blueprint(
        auth_blueprint,
        url_prefix="/api/auth",
    )


    app.register_blueprint(
        admin_blueprint,
        url_prefix="/api/admin",
    )


    app.register_blueprint(
        dashboard_blueprint,
        url_prefix="/api/dashboard",
    )


    app.register_blueprint(
        integrations_blueprint,
        url_prefix="/api/integrations",
    )


    app.register_blueprint(
        infrastructure_blueprint,
        url_prefix="/api/infrastructure",
    )


    app.register_blueprint(
        notifications_blueprint,
        url_prefix="/api/notifications",
    )


    app.register_blueprint(
        projects_blueprint,
        url_prefix="/api/projects",
    )


    app.register_blueprint(
        analysis_blueprint,
        url_prefix="/api/projects",
    )


    # Anciennes routes conservées
    # pendant la transition.
    app.register_blueprint(
        generation_blueprint,
        url_prefix="/api/projects",
    )
    
    app.register_blueprint(
        deployments_blueprint,
        url_prefix="/api/deployments",
    )


    # Nouveau workflow sécurisé
    # des phases 2, 3 et 4.
    app.register_blueprint(
        workflow_blueprint,
        url_prefix="/api/projects",
    )


    register_commands(
        app
    )


    register_workflow_commands(
        app
    )


    register_integration_commands(
        app
    )

    register_deployment_commands(app)


    @app.errorhandler(
        RequestEntityTooLarge
    )
    def request_too_large(
        _error: RequestEntityTooLarge,
    ):
        maximum_bytes = int(
            app.config.get(
                "PROJECT_ARCHIVE_MAX_BYTES",
                100 * 1024 * 1024,
            )
        )


        maximum_megabytes = round(
            maximum_bytes
            / 1024
            / 1024
        )


        return (
            jsonify(
                {
                    "success": False,

                    "error": {
                        "code":
                            "REQUEST_TOO_LARGE",

                        "message": (
                            "La requête dépasse "
                            f"{maximum_megabytes} Mo."
                        ),
                    },
                }
            ),

            413,
        )


    @app.get(
        "/api/health"
    )
    def health():
        if not database_is_available():
            return (
                jsonify(
                    {
                        "success": False,

                        "error": {
                            "code":
                                "DATABASE_UNAVAILABLE",

                            "message": (
                                "PostgreSQL "
                                "est inaccessible."
                            ),
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
                        app.config[
                            "APP_ENV"
                        ],
                },
            }
        )


    return app