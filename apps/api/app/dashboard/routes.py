from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, g, jsonify

from app.auth.decorators import (
    current_user_is_admin,
    require_auth,
)
from app.dashboard.repository import get_dashboard_overview


dashboard_blueprint = Blueprint(
    "dashboard",
    __name__,
)


@dashboard_blueprint.get("/overview")
@require_auth
def overview():
    """
    Retourne les informations de la vue générale.

    Les administrateurs voient les statistiques et intégrations globales.
    Les autres utilisateurs voient uniquement leurs propres ressources.
    """

    user_id = int(g.current_user["id"])

    dashboard_data = get_dashboard_overview(
        owner_user_id=(
            None
            if current_user_is_admin()
            else user_id
        )
    )

    dashboard_data["generatedAt"] = datetime.now(
        timezone.utc,
    ).isoformat()

    dashboard_data["currentUser"] = {
        "id": g.current_user["id"],
        "username": g.current_user["username"],
        "roles": list(
            g.current_user.get("roles") or []
        ),
    }

    integration_services = dashboard_data.pop(
        "integrationServices",
        {},
    )

    dashboard_data["services"] = {
        "api": "online",
        "database": "online",
        "gitlab": integration_services.get(
            "gitlab",
            "not_configured",
        ),
        "nexus": integration_services.get(
            "nexus",
            "not_configured",
        ),
        "argoCd": integration_services.get(
            "argoCd",
            "not_configured",
        ),
        "kubernetes": integration_services.get(
            "kubernetes",
            "not_configured",
        ),
        "ollama": integration_services.get(
            "ollama",
            "not_configured",
        ),
    }

    return jsonify(
        {
            "success": True,
            "data": dashboard_data,
        }
    )
