from __future__ import annotations

from typing import Any

from flask import Blueprint, g, jsonify, request

from app.auth.decorators import (
    current_user_can_access_integration,
    current_user_can_access_project,
    current_user_is_admin,
    require_auth,
)
from app.performance.observability_service import (
    create_observability_stack,
    get_grafana_credentials,
    get_observability_stack,
    list_observability_stacks,
    retry_observability_stack,
)
from app.performance.service import (
    PerformanceServiceError,
    cancel_performance_run,
    create_and_run,
    get_performance_config,
    get_performance_overview,
    get_performance_run,
    list_performance_runs,
    list_performance_tests,
    rerun_performance_run,
)


performance_blueprint = Blueprint("performance", __name__)


def _success(data: dict[str, Any], status: int = 200):
    return jsonify({"success": True, "data": data}), status


def _error_response(code: str, message: str, status: int):
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


def _handle_error(error: Exception):
    if isinstance(error, PerformanceServiceError):
        return _error_response(error.code, error.message, error.http_status)
    raise error


def _current_user_id() -> int:
    return int(g.current_user["id"])


def _owner_filter() -> int | None:
    return None if current_user_is_admin() else _current_user_id()


def _json_payload() -> dict[str, Any]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise PerformanceServiceError(
            "INVALID_JSON",
            "Le corps JSON est invalide.",
        )
    return payload


@performance_blueprint.get("/config")
@require_auth
def config_route():
    try:
        return _success(get_performance_config())
    except Exception as error:
        return _handle_error(error)


@performance_blueprint.get("/overview")
@require_auth
def overview_route():
    try:
        return _success(get_performance_overview(_owner_filter()))
    except Exception as error:
        return _handle_error(error)


@performance_blueprint.get("/tests")
@require_auth
def list_tests_route():
    try:
        return _success(
            {
                "tests": list_performance_tests(
                    owner_user_id=_owner_filter(),
                    search=request.args.get("search"),
                    mode=request.args.get("mode"),
                )
            }
        )
    except Exception as error:
        return _handle_error(error)


@performance_blueprint.post("/tests/run")
@require_auth
def create_and_run_route():
    try:
        payload = _json_payload()

        try:
            project_id = int(payload.get("projectId"))
        except (TypeError, ValueError):
            project_id = 0

        if project_id <= 0 or not current_user_can_access_project(project_id):
            return _error_response(
                "PROJECT_NOT_FOUND",
                "Le projet est introuvable.",
                404,
            )

        run = create_and_run(payload, _current_user_id(), _owner_filter())
        return _success({"run": run}, 201)
    except Exception as error:
        return _handle_error(error)


@performance_blueprint.get("/runs")
@require_auth
def list_runs_route():
    try:
        return _success(
            {
                "runs": list_performance_runs(
                    owner_user_id=_owner_filter(),
                    status=request.args.get("status"),
                    mode=request.args.get("mode"),
                )
            }
        )
    except Exception as error:
        return _handle_error(error)


@performance_blueprint.get("/runs/<int:run_id>")
@require_auth
def run_detail_route(run_id: int):
    try:
        return _success(
            {"run": get_performance_run(run_id, _owner_filter())}
        )
    except Exception as error:
        return _handle_error(error)


@performance_blueprint.post("/runs/<int:run_id>/cancel")
@require_auth
def cancel_run_route(run_id: int):
    try:
        return _success(
            {"run": cancel_performance_run(run_id, _owner_filter())}
        )
    except Exception as error:
        return _handle_error(error)


@performance_blueprint.post("/runs/<int:run_id>/rerun")
@require_auth
def rerun_run_route(run_id: int):
    try:
        return _success(
            {
                "run": rerun_performance_run(
                    run_id,
                    _owner_filter(),
                    _current_user_id(),
                )
            },
            201,
        )
    except Exception as error:
        return _handle_error(error)


@performance_blueprint.get("/observability/stacks")
@require_auth
def list_observability_stacks_route():
    try:
        raw_project_id = request.args.get("projectId")
        project_id = int(raw_project_id) if raw_project_id else None
        if project_id is not None and not current_user_can_access_project(project_id):
            return _error_response("PROJECT_NOT_FOUND", "Le projet est introuvable.", 404)
        return _success(
            {
                "stacks": list_observability_stacks(
                    owner_user_id=_owner_filter(),
                    project_id=project_id,
                )
            }
        )
    except Exception as error:
        return _handle_error(error)


@performance_blueprint.post("/observability/stacks")
@require_auth
def create_observability_stack_route():
    try:
        payload = _json_payload()
        try:
            project_id = int(payload.get("projectId"))
            connection_id = int(payload.get("kubernetesConnectionId"))
        except (TypeError, ValueError):
            project_id = 0
            connection_id = 0

        if project_id <= 0 or not current_user_can_access_project(project_id):
            return _error_response("PROJECT_NOT_FOUND", "Le projet est introuvable.", 404)
        if connection_id <= 0 or not current_user_can_access_integration(connection_id):
            return _error_response(
                "KUBERNETES_CONNECTION_NOT_FOUND",
                "La connexion Kubernetes est introuvable.",
                404,
            )

        stack = create_observability_stack(payload, _current_user_id())
        return _success({"stack": stack}, 201)
    except Exception as error:
        return _handle_error(error)


@performance_blueprint.get("/observability/stacks/<int:stack_id>")
@require_auth
def get_observability_stack_route(stack_id: int):
    try:
        return _success(
            {
                "stack": get_observability_stack(
                    stack_id,
                    owner_user_id=_owner_filter(),
                )
            }
        )
    except Exception as error:
        return _handle_error(error)


@performance_blueprint.post("/observability/stacks/<int:stack_id>/retry")
@require_auth
def retry_observability_stack_route(stack_id: int):
    try:
        return _success(
            {
                "stack": retry_observability_stack(
                    stack_id,
                    owner_user_id=_owner_filter(),
                )
            }
        )
    except Exception as error:
        return _handle_error(error)


@performance_blueprint.get("/observability/stacks/<int:stack_id>/credentials")
@require_auth
def grafana_credentials_route(stack_id: int):
    try:
        return _success(
            {
                "credentials": get_grafana_credentials(
                    stack_id,
                    owner_user_id=_owner_filter(),
                )
            }
        )
    except Exception as error:
        return _handle_error(error)
