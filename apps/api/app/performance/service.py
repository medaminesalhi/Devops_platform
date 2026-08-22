from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from flask import current_app

from app.performance import repository


TEST_TYPES = {"smoke", "load", "stress", "spike", "soak", "custom"}
MODES = {"basic", "observability"}
RUN_STATUSES = {"queued", "running", "passed", "failed", "cancelled"}

DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
DNS_HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)*"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$"
)


class PerformanceServiceError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        http_status: int = 400,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _integer(
    value: Any,
    *,
    field: str,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise PerformanceServiceError(
            "INVALID_PERFORMANCE_CONFIGURATION",
            f"Le champ {field} doit être un entier.",
        ) from error

    if parsed < minimum or parsed > maximum:
        raise PerformanceServiceError(
            "INVALID_PERFORMANCE_CONFIGURATION",
            f"Le champ {field} doit être compris entre {minimum} et {maximum}.",
        )
    return parsed


def _float(
    value: Any,
    *,
    field: str,
    minimum: float,
    maximum: float,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise PerformanceServiceError(
            "INVALID_PERFORMANCE_CONFIGURATION",
            f"Le champ {field} doit être numérique.",
        ) from error

    if parsed < minimum or parsed > maximum:
        raise PerformanceServiceError(
            "INVALID_PERFORMANCE_CONFIGURATION",
            f"Le champ {field} doit être compris entre {minimum} et {maximum}.",
        )
    return parsed


def _normalize_http_url(
    raw_value: Any,
    *,
    field_name: str,
    required: bool = True,
) -> str | None:
    value = str(raw_value or "").strip()
    if not value:
        if required:
            raise PerformanceServiceError(
                "URL_REQUIRED",
                f"Le champ {field_name} est obligatoire.",
            )
        return None

    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"}:
        raise PerformanceServiceError(
            "INVALID_URL",
            f"Le champ {field_name} doit utiliser http ou https.",
        )

    if not parts.hostname:
        raise PerformanceServiceError(
            "INVALID_URL",
            f"Le champ {field_name} ne contient pas de nom d'hôte valide.",
        )

    if parts.username or parts.password:
        raise PerformanceServiceError(
            "INVALID_URL",
            f"Ne placez pas d'identifiant ou mot de passe dans {field_name}.",
        )

    try:
        _ = parts.port
    except ValueError as error:
        raise PerformanceServiceError(
            "INVALID_URL",
            f"Le port de {field_name} est invalide.",
        ) from error

    # Les fragments ne sont jamais envoyés par HTTP.
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))


def _normalize_target_url(raw_value: Any) -> str:
    value = _normalize_http_url(
        raw_value,
        field_name="URL cible",
        required=True,
    )
    assert value is not None

    hostname = (urlsplit(value).hostname or "").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise PerformanceServiceError(
            "TARGET_LOCALHOST_NOT_ALLOWED",
            "La cible localhost n'est pas autorisée depuis le worker k6.",
            403,
        )

    return value


def _validate_namespace(value: Any, *, required: bool = False) -> str | None:
    namespace = str(value or "").strip().lower()
    if not namespace:
        if required:
            raise PerformanceServiceError(
                "OBSERVABILITY_NAMESPACE_REQUIRED",
                "Le namespace Kubernetes d'observabilité est obligatoire.",
            )
        return None

    if len(namespace) > 63 or not DNS_LABEL_RE.fullmatch(namespace):
        raise PerformanceServiceError(
            "INVALID_OBSERVABILITY_NAMESPACE",
            "Le namespace Kubernetes doit être un label DNS-1123 valide.",
        )
    return namespace


def _validate_dashboard_uid(value: Any) -> str:
    uid = str(value or "k6-performance").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", uid):
        raise PerformanceServiceError(
            "INVALID_GRAFANA_DASHBOARD_UID",
            "Le Dashboard UID Grafana est invalide.",
        )
    return uid


def _validate_observability(mode: str, raw: Any) -> dict[str, Any] | None:
    if mode == "basic":
        return None

    if not isinstance(raw, dict):
        raise PerformanceServiceError(
            "OBSERVABILITY_CONFIGURATION_REQUIRED",
            "La configuration Prometheus/Grafana est obligatoire en mode observability.",
        )

    prometheus_url = _normalize_http_url(
        raw.get("prometheusRemoteWriteUrl"),
        field_name="URL Prometheus Remote Write",
        required=True,
    )
    grafana_base_url = _normalize_http_url(
        raw.get("grafanaBaseUrl"),
        field_name="URL Grafana",
        required=False,
    )

    return {
        "namespace": _validate_namespace(raw.get("namespace"), required=False),
        "retentionDays": _integer(
            raw.get("retentionDays", 7),
            field="retentionDays",
            minimum=1,
            maximum=int(current_app.config.get("PERFORMANCE_MAX_RETENTION_DAYS", 90)),
        ),
        "prometheusRemoteWriteUrl": prometheus_url,
        "grafanaBaseUrl": grafana_base_url,
        "grafanaDashboardUid": _validate_dashboard_uid(
            raw.get("grafanaDashboardUid")
        ),
    }


def _validate_request(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        project_id = int(payload.get("projectId"))
    except (TypeError, ValueError) as error:
        raise PerformanceServiceError(
            "INVALID_PROJECT",
            "Le projet est invalide.",
        ) from error

    if project_id <= 0:
        raise PerformanceServiceError("INVALID_PROJECT", "Le projet est invalide.")

    deployment_id: int | None = None
    raw_deployment_id = payload.get("deploymentId")
    if raw_deployment_id not in (None, ""):
        try:
            deployment_id = int(raw_deployment_id)
        except (TypeError, ValueError) as error:
            raise PerformanceServiceError(
                "INVALID_DEPLOYMENT",
                "Le déploiement est invalide.",
            ) from error
        if deployment_id <= 0:
            raise PerformanceServiceError("INVALID_DEPLOYMENT", "Le déploiement est invalide.")

    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 180:
        raise PerformanceServiceError(
            "INVALID_TEST_NAME",
            "Le nom du test doit contenir entre 1 et 180 caractères.",
        )

    description_value = payload.get("description")
    description = str(description_value).strip() if description_value is not None else None
    description = description or None
    if description and len(description) > 4000:
        raise PerformanceServiceError(
            "INVALID_DESCRIPTION",
            "La description ne peut pas dépasser 4000 caractères.",
        )

    test_type = str(payload.get("testType") or "").strip().lower()
    if test_type not in TEST_TYPES:
        raise PerformanceServiceError(
            "INVALID_TEST_TYPE",
            "Le type de test k6 est invalide.",
        )

    mode = str(payload.get("mode") or "").strip().lower()
    if mode not in MODES:
        raise PerformanceServiceError(
            "INVALID_PERFORMANCE_MODE",
            "Le mode de performance est invalide.",
        )

    if payload.get("authorizationConfirmed") is not True:
        raise PerformanceServiceError(
            "TARGET_AUTHORIZATION_REQUIRED",
            "Confirmez que vous êtes autorisé à exécuter un test de charge sur cette cible.",
            403,
        )

    raw_load = payload.get("loadProfile")
    if not isinstance(raw_load, dict):
        raise PerformanceServiceError(
            "INVALID_LOAD_PROFILE",
            "Le profil de charge est obligatoire.",
        )

    max_vus_limit = int(current_app.config.get("PERFORMANCE_MAX_VUS", 500))
    max_duration_limit = int(current_app.config.get("PERFORMANCE_MAX_DURATION_SECONDS", 3600))

    virtual_users = _integer(
        raw_load.get("virtualUsers"),
        field="virtualUsers",
        minimum=1,
        maximum=max_vus_limit,
    )
    max_virtual_users = _integer(
        raw_load.get("maxVirtualUsers"),
        field="maxVirtualUsers",
        minimum=1,
        maximum=max_vus_limit,
    )
    if max_virtual_users < virtual_users:
        raise PerformanceServiceError(
            "INVALID_LOAD_PROFILE",
            "maxVirtualUsers doit être supérieur ou égal à virtualUsers.",
        )

    duration_seconds = _integer(
        raw_load.get("durationSeconds"),
        field="durationSeconds",
        minimum=1,
        maximum=max_duration_limit,
    )

    raw_thresholds = payload.get("thresholds")
    if not isinstance(raw_thresholds, dict):
        raise PerformanceServiceError(
            "INVALID_THRESHOLDS",
            "Les thresholds k6 sont obligatoires.",
        )

    thresholds = {
        "errorRatePercent": _float(
            raw_thresholds.get("errorRatePercent"),
            field="errorRatePercent",
            minimum=0.0,
            maximum=100.0,
        ),
        "p95Ms": _integer(
            raw_thresholds.get("p95Ms"),
            field="p95Ms",
            minimum=1,
            maximum=600_000,
        ),
        "p99Ms": _integer(
            raw_thresholds.get("p99Ms"),
            field="p99Ms",
            minimum=1,
            maximum=600_000,
        ),
        "checksRatePercent": _float(
            raw_thresholds.get("checksRatePercent"),
            field="checksRatePercent",
            minimum=0.0,
            maximum=100.0,
        ),
    }

    if thresholds["p99Ms"] < thresholds["p95Ms"]:
        raise PerformanceServiceError(
            "INVALID_THRESHOLDS",
            "Le seuil p99 doit être supérieur ou égal au seuil p95.",
        )

    observability_stack_id: int | None = None
    if mode == "observability":
        raw_stack_id = payload.get("observabilityStackId")
        if raw_stack_id in (None, "") and isinstance(payload.get("observability"), dict):
            raw_stack_id = payload["observability"].get("stackId")
        try:
            observability_stack_id = int(raw_stack_id)
        except (TypeError, ValueError) as error:
            raise PerformanceServiceError(
                "OBSERVABILITY_STACK_REQUIRED",
                "Sélectionnez une stack Prometheus/Grafana prête avant de lancer le test.",
            ) from error
        if observability_stack_id <= 0:
            raise PerformanceServiceError(
                "OBSERVABILITY_STACK_REQUIRED",
                "Sélectionnez une stack Prometheus/Grafana prête avant de lancer le test.",
            )

    return {
        "project_id": project_id,
        "deployment_id": deployment_id,
        "name": name,
        "description": description,
        "target_url": _normalize_target_url(payload.get("targetUrl")),
        "test_type": test_type,
        "mode": mode,
        "load_profile": {
            "virtualUsers": virtual_users,
            "maxVirtualUsers": max_virtual_users,
            "durationSeconds": duration_seconds,
        },
        "thresholds": thresholds,
        "observability_stack_id": observability_stack_id,
    }


def _serialize_metrics(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    defaults = {
        "requests": 0,
        "rps": 0.0,
        "avgMs": 0.0,
        "minMs": 0.0,
        "maxMs": 0.0,
        "p90Ms": 0.0,
        "p95Ms": 0.0,
        "p99Ms": 0.0,
        "errorRatePercent": 0.0,
        "checksRatePercent": 0.0,
        "dataReceivedBytes": 0,
        "dataSentBytes": 0,
        "iterations": 0,
    }
    defaults.update(value)
    return defaults


def _empty_threshold_results(thresholds: Any) -> list[dict[str, Any]]:
    thresholds = thresholds if isinstance(thresholds, dict) else {}
    return [
        {
            "key": "error_rate",
            "label": "Taux d’erreur",
            "expected": f"< {thresholds.get('errorRatePercent', 0)} %",
            "actual": "En attente",
            "passed": False,
        },
        {
            "key": "p95",
            "label": "Latence p95",
            "expected": f"< {thresholds.get('p95Ms', 0)} ms",
            "actual": "En attente",
            "passed": False,
        },
        {
            "key": "p99",
            "label": "Latence p99",
            "expected": f"< {thresholds.get('p99Ms', 0)} ms",
            "actual": "En attente",
            "passed": False,
        },
        {
            "key": "checks",
            "label": "Checks réussis",
            "expected": f"> {thresholds.get('checksRatePercent', 0)} %",
            "actual": "En attente",
            "passed": False,
        },
    ]


def serialize_run_summary(row: dict[str, Any]) -> dict[str, Any]:
    load_profile = row.get("load_profile") or {}
    return {
        "id": int(row["id"]),
        "testId": int(row["test_id"]),
        "testName": row.get("test_name") or "",
        "projectId": int(row["project_id"]),
        "projectName": row.get("project_name") or f"Projet #{row['project_id']}",
        "deploymentId": int(row["deployment_id"]) if row.get("deployment_id") else None,
        "mode": row.get("mode") or "basic",
        "testType": row.get("test_type") or "smoke",
        "status": row.get("status") or "queued",
        "targetUrl": row.get("target_url") or "",
        "createdAt": _iso(row.get("created_at")),
        "startedAt": _iso(row.get("started_at")),
        "finishedAt": _iso(row.get("finished_at")),
        "durationSeconds": int(load_profile.get("durationSeconds") or 0),
        "maxVirtualUsers": int(load_profile.get("maxVirtualUsers") or 0),
        "metrics": _serialize_metrics(row.get("metrics")),
        "grafanaDashboardUrl": row.get("grafana_dashboard_url"),
    }


def serialize_run(row: dict[str, Any]) -> dict[str, Any]:
    run_id = int(row["id"])
    result = serialize_run_summary(row)
    result.update(
        {
            "thresholds": (
                row.get("threshold_results")
                if isinstance(row.get("threshold_results"), list)
                else _empty_threshold_results(row.get("thresholds"))
            ),
            "observability": row.get("observability"),
            "samples": [
                {
                    "id": int(sample["id"]),
                    "sampledAt": _iso(sample.get("sampled_at")),
                    "elapsedSeconds": int(sample.get("elapsed_seconds") or 0),
                    "vus": int(sample.get("vus") or 0),
                    "requests": int(sample.get("requests") or 0),
                    "requestsTotal": int(sample.get("requests_total") or 0),
                    "iterationsTotal": int(sample.get("iterations_total") or 0),
                    "rps": float(sample.get("rps") or 0.0),
                    "avgMs": float(sample.get("avg_ms") or 0.0),
                    "p95Ms": float(sample.get("p95_ms") or 0.0),
                    "p99Ms": float(sample.get("p99_ms") or 0.0),
                    "errorRatePercent": float(sample.get("error_rate_percent") or 0.0),
                    "checksRatePercent": float(sample.get("checks_rate_percent") or 0.0),
                }
                for sample in repository.list_run_samples(run_id)
            ],
            "logs": [
                {
                    "id": int(log["id"]),
                    "createdAt": _iso(log.get("created_at")),
                    "level": log.get("level") or "info",
                    "message": log.get("message") or "",
                }
                for log in repository.list_run_logs(run_id)
            ],
            "errorCode": row.get("error_code"),
            "errorMessage": row.get("error_message"),
        }
    )
    return result


def _serialize_test(row: dict[str, Any]) -> dict[str, Any]:
    last_run = None
    if row.get("last_run_id") is not None:
        last_run = {
            "id": int(row["last_run_id"]),
            "testId": int(row["id"]),
            "testName": row.get("name") or "",
            "projectId": int(row["project_id"]),
            "projectName": row.get("project_name") or f"Projet #{row['project_id']}",
            "deploymentId": int(row["deployment_id"]) if row.get("deployment_id") else None,
            "mode": row.get("mode") or "basic",
            "testType": row.get("test_type") or "smoke",
            "status": row.get("last_run_status") or "queued",
            "targetUrl": row.get("target_url") or "",
            "createdAt": _iso(row.get("last_run_created_at")),
            "startedAt": _iso(row.get("last_run_started_at")),
            "finishedAt": _iso(row.get("last_run_finished_at")),
            "durationSeconds": int((row.get("load_profile") or {}).get("durationSeconds") or 0),
            "maxVirtualUsers": int((row.get("load_profile") or {}).get("maxVirtualUsers") or 0),
            "metrics": _serialize_metrics(row.get("last_run_metrics")),
            "grafanaDashboardUrl": row.get("last_run_grafana_dashboard_url"),
        }

    return {
        "id": int(row["id"]),
        "projectId": int(row["project_id"]),
        "projectName": row.get("project_name") or f"Projet #{row['project_id']}",
        "deploymentId": int(row["deployment_id"]) if row.get("deployment_id") else None,
        "name": row.get("name") or "",
        "description": row.get("description"),
        "targetUrl": row.get("target_url") or "",
        "testType": row.get("test_type") or "smoke",
        "mode": row.get("mode") or "basic",
        "loadProfile": row.get("load_profile") or {},
        "thresholds": row.get("thresholds") or {},
        "observability": row.get("observability"),
        "createdAt": _iso(row.get("created_at")),
        "updatedAt": _iso(row.get("updated_at")),
        "lastRun": last_run,
    }


def create_and_run(
    payload: dict[str, Any],
    created_by: int,
    owner_user_id: int | None,
) -> dict[str, Any]:
    values = _validate_request(payload)

    project = repository.find_project(values["project_id"])
    if project is None:
        raise PerformanceServiceError("PROJECT_NOT_FOUND", "Le projet est introuvable.", 404)

    deployment_id = values["deployment_id"]
    if deployment_id is not None:
        deployment = repository.find_deployment(deployment_id)
        if deployment is None or int(deployment["project_id"]) != values["project_id"]:
            raise PerformanceServiceError(
                "DEPLOYMENT_NOT_FOUND",
                "Le déploiement est introuvable pour ce projet.",
                404,
            )
        if deployment.get("status") != "succeeded":
            raise PerformanceServiceError(
                "DEPLOYMENT_NOT_READY",
                "Un test lié à un déploiement nécessite un déploiement réussi.",
                409,
            )

    observability_stack_id = values.pop("observability_stack_id", None)
    if values["mode"] == "observability":
        from app.performance.observability_service import (
            resolve_stack_observability_config,
        )

        assert observability_stack_id is not None
        values["observability"] = resolve_stack_observability_config(
            observability_stack_id,
            owner_user_id=owner_user_id,
            project_id=values["project_id"],
        )
    else:
        values["observability"] = None

    run = repository.create_test_and_run(created_by=created_by, **values)
    return serialize_run(run)


def list_performance_tests(
    *,
    owner_user_id: int | None,
    search: str | None,
    mode: str | None,
) -> list[dict[str, Any]]:
    normalized_mode = str(mode).strip().lower() if mode else None
    if normalized_mode and normalized_mode not in MODES:
        raise PerformanceServiceError("INVALID_FILTER", "Le filtre mode est invalide.")

    rows = repository.list_tests(
        owner_user_id=owner_user_id,
        search=str(search).strip() if search else None,
        mode=normalized_mode,
    )
    return [_serialize_test(row) for row in rows]


def list_performance_runs(
    *,
    owner_user_id: int | None,
    status: str | None,
    mode: str | None,
) -> list[dict[str, Any]]:
    normalized_status = str(status).strip().lower() if status else None
    normalized_mode = str(mode).strip().lower() if mode else None

    if normalized_status and normalized_status not in RUN_STATUSES:
        raise PerformanceServiceError("INVALID_FILTER", "Le filtre status est invalide.")
    if normalized_mode and normalized_mode not in MODES:
        raise PerformanceServiceError("INVALID_FILTER", "Le filtre mode est invalide.")

    return [
        serialize_run_summary(row)
        for row in repository.list_runs(
            owner_user_id=owner_user_id,
            status=normalized_status,
            mode=normalized_mode,
        )
    ]


def get_performance_run(run_id: int, owner_user_id: int | None) -> dict[str, Any]:
    row = repository.find_run(run_id, owner_user_id=owner_user_id)
    if row is None:
        raise PerformanceServiceError(
            "PERFORMANCE_RUN_NOT_FOUND",
            "Le run de performance est introuvable.",
            404,
        )
    return serialize_run(row)


def cancel_performance_run(run_id: int, owner_user_id: int | None) -> dict[str, Any]:
    current = repository.find_run(run_id, owner_user_id=owner_user_id)
    if current is None:
        raise PerformanceServiceError(
            "PERFORMANCE_RUN_NOT_FOUND",
            "Le run de performance est introuvable.",
            404,
        )

    if current.get("status") not in {"queued", "running"}:
        raise PerformanceServiceError(
            "PERFORMANCE_RUN_NOT_CANCELLABLE",
            "Ce run est déjà terminé et ne peut plus être annulé.",
            409,
        )

    updated = repository.request_cancellation(run_id)
    if updated is None:
        raise PerformanceServiceError(
            "PERFORMANCE_RUN_NOT_CANCELLABLE",
            "Ce run ne peut plus être annulé.",
            409,
        )

    repository.add_log(
        run_id,
        level="warning",
        message=(
            "Exécution annulée par l'utilisateur."
            if updated.get("status") == "cancelled"
            else "Annulation demandée par l'utilisateur."
        ),
    )
    refreshed = repository.find_run(run_id)
    if refreshed is None:
        raise PerformanceServiceError(
            "PERFORMANCE_RUN_NOT_FOUND",
            "Le run de performance est introuvable.",
            404,
        )
    return serialize_run(refreshed)


def rerun_performance_run(
    run_id: int,
    owner_user_id: int | None,
    created_by: int,
) -> dict[str, Any]:
    current = repository.find_run(run_id, owner_user_id=owner_user_id)
    if current is None:
        raise PerformanceServiceError(
            "PERFORMANCE_RUN_NOT_FOUND",
            "Le run de performance est introuvable.",
            404,
        )

    if current.get("status") in {"queued", "running"}:
        raise PerformanceServiceError(
            "PERFORMANCE_RUN_ALREADY_ACTIVE",
            "Ce run est encore actif. Annulez-le ou attendez sa fin avant de le relancer.",
            409,
        )

    created = repository.create_rerun_from_existing(
        source_run=current,
        created_by=created_by,
    )
    return serialize_run(created)


def get_performance_config() -> dict[str, Any]:
    return {
        "limits": {
            "maxVirtualUsers": int(current_app.config.get("PERFORMANCE_MAX_VUS", 500)),
            "maxDurationSeconds": int(
                current_app.config.get("PERFORMANCE_MAX_DURATION_SECONDS", 3600)
            ),
            "maxRetentionDays": int(
                current_app.config.get("PERFORMANCE_MAX_RETENTION_DAYS", 90)
            ),
        },
        "targetPolicy": {
            "configuredFromInterface": True,
            "allowlistRequired": False,
            "authorizationConfirmationRequired": True,
        },
        "observability": {
            "configuredFromInterface": True,
            "managedProvisioning": True,
            "stackRequired": True,
            "prometheusRemoteWriteUrlRequired": False,
            "grafanaBaseUrlRequired": False,
        },
    }


def get_performance_overview(owner_user_id: int | None) -> dict[str, int]:
    values = repository.get_overview(owner_user_id)
    return {
        "totalTests": values["total_tests"],
        "totalRuns": values["total_runs"],
        "runningRuns": values["running_runs"],
        "passedRuns": values["passed_runs"],
        "failedRuns": values["failed_runs"],
    }
