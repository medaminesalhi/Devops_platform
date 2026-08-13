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

    Les administrateurs voient les statistiques globales. Les autres
    utilisateurs voient uniquement leurs projets et leurs déploiements.
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
