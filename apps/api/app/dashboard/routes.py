from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, g, jsonify

from app.auth.decorators import require_auth
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
    """

    dashboard_data = get_dashboard_overview()

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

    dashboard_data["services"] = {
        "api": "online",
        "database": "online",
        "gitlab": "not_configured",
        "argoCd": "not_configured",
    }

    return jsonify(
        {
            "success": True,
            "data": dashboard_data,
        }
    )