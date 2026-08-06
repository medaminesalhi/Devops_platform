from __future__ import annotations

from typing import Any

from flask import Blueprint, g, jsonify, request

from app.auth.decorators import require_auth
from app.deployments.service import (
    DeploymentServiceError,
    approve_correction,
    cancel_deployment,
    confirm_synchronization,
    create_deployment,
    get_deployment,
    get_options,
    get_project_readiness,
    list_deployments,
    request_diagnosis,
    retry_deployment,
    send_diagnostic_message,
    start_deployment,
)


deployments_blueprint = Blueprint(
    "deployments",
    __name__,
)


def _error_response(
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


def _success(data: dict[str, Any], status: int = 200):
    return jsonify({"success": True, "data": data}), status


def _current_user_id() -> int:
    return int(g.current_user["id"])


def _optional_integer(name: str) -> int | None:
    raw = request.args.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw)
    except ValueError as error:
        raise DeploymentServiceError(
            "INVALID_FILTER",
            f"Le filtre {name} est invalide.",
        ) from error
    return value if value > 0 else None


def _json_payload() -> dict[str, Any]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise DeploymentServiceError(
            "INVALID_JSON",
            "Le corps JSON est invalide.",
        )
    return payload


def _handle_error(error: Exception):
    if isinstance(error, DeploymentServiceError):
        return _error_response(
            error.code,
            error.message,
            error.http_status,
        )
    raise error


@deployments_blueprint.get("")
@require_auth
def list_deployments_route():
    try:
        result = list_deployments(
            {
                "search": request.args.get("search"),
                "project_id": _optional_integer("projectId"),
                "environment_id": _optional_integer("environmentId"),
                "status": request.args.get("status"),
                "date_from": request.args.get("dateFrom"),
                "date_to": request.args.get("dateTo"),
            }
        )
        return _success(result)
    except Exception as error:
        return _handle_error(error)


@deployments_blueprint.get("/options")
@require_auth
def deployment_options_route():
    try:
        return _success({"projects": get_options()})
    except Exception as error:
        return _handle_error(error)


@deployments_blueprint.get("/projects/<int:project_id>/readiness")
@require_auth
def project_readiness_route(project_id: int):
    try:
        return _success(
            {"readiness": get_project_readiness(project_id)}
        )
    except Exception as error:
        return _handle_error(error)


@deployments_blueprint.post("")
@require_auth
def create_deployment_route():
    try:
        deployment = create_deployment(
            _json_payload(),
            _current_user_id(),
        )
        return _success({"deployment": deployment}, 201)
    except Exception as error:
        return _handle_error(error)


@deployments_blueprint.get("/<int:deployment_id>")
@require_auth
def deployment_detail_route(deployment_id: int):
    try:
        return _success(
            {"deployment": get_deployment(deployment_id)}
        )
    except Exception as error:
        return _handle_error(error)


@deployments_blueprint.post("/<int:deployment_id>/start")
@require_auth
def start_deployment_route(deployment_id: int):
    try:
        return _success(
            {"deployment": start_deployment(deployment_id)}
        )
    except Exception as error:
        return _handle_error(error)


@deployments_blueprint.post("/<int:deployment_id>/cancel")
@require_auth
def cancel_deployment_route(deployment_id: int):
    try:
        return _success(
            {"deployment": cancel_deployment(deployment_id)}
        )
    except Exception as error:
        return _handle_error(error)


@deployments_blueprint.post("/<int:deployment_id>/retry")
@require_auth
def retry_deployment_route(deployment_id: int):
    try:
        return _success(
            {"deployment": retry_deployment(deployment_id)}
        )
    except Exception as error:
        return _handle_error(error)


@deployments_blueprint.post("/<int:deployment_id>/confirm-sync")
@require_auth
def confirm_sync_route(deployment_id: int):
    try:
        return _success(
            {"deployment": confirm_synchronization(deployment_id)}
        )
    except Exception as error:
        return _handle_error(error)


@deployments_blueprint.post("/<int:deployment_id>/diagnostic")
@require_auth
def diagnose_deployment_route(deployment_id: int):
    try:
        return _success(
            {"diagnostic": request_diagnosis(deployment_id)}
        )
    except Exception as error:
        return _handle_error(error)


@deployments_blueprint.post(
    "/<int:deployment_id>/diagnostic/messages"
)
@require_auth
def deployment_chat_route(deployment_id: int):
    try:
        payload = _json_payload()
        content = str(payload.get("content") or "")
        messages = send_diagnostic_message(
            deployment_id=deployment_id,
            content=content,
            user_id=_current_user_id(),
        )
        return _success({"messages": messages})
    except Exception as error:
        return _handle_error(error)


@deployments_blueprint.post(
    "/<int:deployment_id>/corrections/<int:correction_id>/approve"
)
@require_auth
def approve_correction_route(
    deployment_id: int,
    correction_id: int,
):
    try:
        diagnostic = approve_correction(
            deployment_id=deployment_id,
            correction_id=correction_id,
            user_id=_current_user_id(),
        )
        return _success({"diagnostic": diagnostic})
    except Exception as error:
        return _handle_error(error)
