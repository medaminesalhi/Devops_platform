from __future__ import annotations

from typing import Any

from flask import (
    Blueprint,
    g,
    jsonify,
    request,
)

from app.admin.repository import (
    activate_user,
    approve_user,
    find_user_detail,
    get_admin_summary,
    list_roles,
    list_user_deployments,
    list_user_logins,
    list_users,
    reject_user,
    set_user_role,
    suspend_user,
)
from app.auth.decorators import require_roles
from app.auth.repository import create_audit_log


admin_blueprint = Blueprint(
    "admin",
    __name__,
)

VALID_STATUSES = {
    "pending",
    "active",
    "rejected",
    "suspended",
}

VALID_ROLES = {
    "admin",
    "devops",
    "developer",
    "viewer",
}


def error_response(
    code: str,
    message: str,
    status: int,
):
    return (
        jsonify(
            {
                "success": False,
                "error": {
                    "code": code,
                    "message": message,
                },
            }
        ),
        status,
    )


def user_to_json(
    user: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "firstName": user.get("first_name"),
        "lastName": user.get("last_name"),
        "company": user.get("company"),
        "status": user.get("status"),
        "isActive": bool(user.get("is_active")),
        "roles": list(user.get("roles") or []),
        "lastLoginAt": user.get("last_login_at"),
        "createdAt": user.get("created_at"),
        "updatedAt": user.get("updated_at"),
        "approvedAt": user.get("approved_at"),
        "approvedBy": user.get("approved_by"),
        "rejectedAt": user.get("rejected_at"),
        "rejectionReason": user.get("rejection_reason"),
        "suspendedAt": user.get("suspended_at"),
        "deploymentCount": int(user.get("deployment_count") or 0),
        "lastDeploymentAt": user.get("last_deployment_at"),
        "successfulLoginCount": int(user.get("successful_login_count") or 0),
    }


def login_to_json(
    item: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": item["id"],
        "success": bool(item["success"]),
        "failureReason": item.get("failure_reason"),
        "ipAddress": item.get("ip_address"),
        "userAgent": item.get("user_agent"),
        "loggedAt": item.get("logged_at"),
    }


def deployment_to_json(
    item: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": item["id"],
        "projectId": item["project_id"],
        "projectName": item.get("project_name"),
        "environmentId": item.get("environment_id"),
        "environmentName": item.get("environment_name"),
        "environmentCode": item.get("environment_code"),
        "version": item.get("version"),
        "status": item.get("status"),
        "progress": int(item.get("progress") or 0),
        "currentStage": item.get("current_stage"),
        "currentStageLabel": item.get("current_stage_label"),
        "createdAt": item.get("created_at"),
        "startedAt": item.get("started_at"),
        "finishedAt": item.get("finished_at"),
        "errorCode": item.get("error_code"),
        "errorMessage": item.get("error_message"),
    }


def read_json_payload() -> dict[str, Any] | None:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else None


def current_admin_id() -> int:
    return int(g.current_user["id"])


def prevent_self_lockout(
    target_user_id: int,
):
    if target_user_id == current_admin_id():
        return error_response(
            "SELF_LOCKOUT_FORBIDDEN",
            "Vous ne pouvez pas suspendre, refuser ou retirer votre propre rôle administrateur.",
            400,
        )
    return None


@admin_blueprint.get("/overview")
@require_roles("admin")
def overview():
    return jsonify(
        {
            "success": True,
            "data": {
                "summary": get_admin_summary(),
                "roles": [
                    {
                        "code": role["code"],
                        "name": role["name"],
                        "description": role.get("description"),
                    }
                    for role in list_roles()
                ],
            },
        }
    )


@admin_blueprint.get("/users")
@require_roles("admin")
def users():
    search = request.args.get("search", "").strip() or None
    status = request.args.get("status", "").strip().lower() or None
    role = request.args.get("role", "").strip().lower() or None

    if status and status not in VALID_STATUSES:
        return error_response(
            "INVALID_STATUS",
            "Le statut demandé est invalide.",
            400,
        )

    if role and role not in VALID_ROLES:
        return error_response(
            "INVALID_ROLE",
            "Le rôle demandé est invalide.",
            400,
        )

    rows = list_users(
        search=search,
        status=status,
        role=role,
    )

    return jsonify(
        {
            "success": True,
            "data": {
                "users": [user_to_json(row) for row in rows],
            },
        }
    )


@admin_blueprint.get("/users/<int:user_id>")
@require_roles("admin")
def user_detail(user_id: int):
    user = find_user_detail(user_id)

    if user is None:
        return error_response(
            "USER_NOT_FOUND",
            "L'utilisateur demandé n'existe pas.",
            404,
        )

    logins = list_user_logins(user_id)
    deployments = list_user_deployments(user_id)

    return jsonify(
        {
            "success": True,
            "data": {
                "user": user_to_json(user),
                "logins": [login_to_json(item) for item in logins],
                "deployments": [deployment_to_json(item) for item in deployments],
            },
        }
    )


@admin_blueprint.post("/users/<int:user_id>/approve")
@require_roles("admin")
def approve(user_id: int):
    self_lockout = prevent_self_lockout(user_id)
    if self_lockout is not None:
        return self_lockout

    payload = read_json_payload()

    if payload is None:
        return error_response("INVALID_JSON", "Le corps JSON est invalide.", 400)

    role_code = str(payload.get("roleCode", "viewer")).strip().lower()

    if role_code not in VALID_ROLES:
        return error_response("INVALID_ROLE", "Le rôle demandé est invalide.", 400)

    try:
        updated = approve_user(
            user_id=user_id,
            role_code=role_code,
            approved_by=current_admin_id(),
        )
    except ValueError as error:
        return error_response("INVALID_ROLE", str(error), 400)

    if not updated:
        return error_response("USER_NOT_FOUND", "L'utilisateur demandé n'existe pas.", 404)

    create_audit_log(
        actor_user_id=current_admin_id(),
        action="USER_APPROVED",
        resource_type="user",
        resource_id=user_id,
        metadata={"roleCode": role_code},
    )

    return jsonify({"success": True, "data": {"message": "Compte approuvé."}})


@admin_blueprint.post("/users/<int:user_id>/reject")
@require_roles("admin")
def reject(user_id: int):
    self_lockout = prevent_self_lockout(user_id)
    if self_lockout is not None:
        return self_lockout

    payload = read_json_payload() or {}
    reason = str(payload.get("reason", "")).strip()[:1000] or None

    if not reject_user(user_id=user_id, reason=reason):
        return error_response("USER_NOT_FOUND", "L'utilisateur demandé n'existe pas.", 404)

    create_audit_log(
        actor_user_id=current_admin_id(),
        action="USER_REJECTED",
        resource_type="user",
        resource_id=user_id,
        metadata={"reason": reason},
    )

    return jsonify({"success": True, "data": {"message": "Compte refusé."}})


@admin_blueprint.post("/users/<int:user_id>/suspend")
@require_roles("admin")
def suspend(user_id: int):
    self_lockout = prevent_self_lockout(user_id)
    if self_lockout is not None:
        return self_lockout

    if not suspend_user(user_id=user_id):
        return error_response("USER_NOT_FOUND", "L'utilisateur demandé n'existe pas.", 404)

    create_audit_log(
        actor_user_id=current_admin_id(),
        action="USER_SUSPENDED",
        resource_type="user",
        resource_id=user_id,
    )

    return jsonify({"success": True, "data": {"message": "Compte suspendu."}})


@admin_blueprint.post("/users/<int:user_id>/activate")
@require_roles("admin")
def activate(user_id: int):
    if not activate_user(
        user_id=user_id,
        approved_by=current_admin_id(),
    ):
        return error_response("USER_NOT_FOUND", "L'utilisateur demandé n'existe pas.", 404)

    create_audit_log(
        actor_user_id=current_admin_id(),
        action="USER_ACTIVATED",
        resource_type="user",
        resource_id=user_id,
    )

    return jsonify({"success": True, "data": {"message": "Compte réactivé."}})


@admin_blueprint.put("/users/<int:user_id>/role")
@require_roles("admin")
def change_role(user_id: int):
    payload = read_json_payload()

    if payload is None:
        return error_response("INVALID_JSON", "Le corps JSON est invalide.", 400)

    role_code = str(payload.get("roleCode", "")).strip().lower()

    if role_code not in VALID_ROLES:
        return error_response("INVALID_ROLE", "Le rôle demandé est invalide.", 400)

    if user_id == current_admin_id() and role_code != "admin":
        return error_response(
            "SELF_LOCKOUT_FORBIDDEN",
            "Vous ne pouvez pas retirer votre propre rôle administrateur.",
            400,
        )

    try:
        updated = set_user_role(
            user_id=user_id,
            role_code=role_code,
        )
    except ValueError as error:
        return error_response("INVALID_ROLE", str(error), 400)

    if not updated:
        return error_response("USER_NOT_FOUND", "L'utilisateur demandé n'existe pas.", 404)

    create_audit_log(
        actor_user_id=current_admin_id(),
        action="USER_ROLE_CHANGED",
        resource_type="user",
        resource_id=user_id,
        metadata={"roleCode": role_code},
    )

    return jsonify({"success": True, "data": {"message": "Rôle mis à jour."}})
