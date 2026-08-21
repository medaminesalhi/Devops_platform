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


def _normalize_target_url(raw_value: Any) -> str:
    value = str(raw_value or "").strip()
    if not value:
        raise PerformanceServiceError(
            "TARGET_URL_REQUIRED",
            "L'URL cible du test est obligatoire.",
        )

    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"}:
        raise PerformanceServiceError(
            "INVALID_TARGET_URL",
            "L'URL cible doit utiliser http ou https.",
        )

    if not parts.hostname:
        raise PerformanceServiceError(
            "INVALID_TARGET_URL",
            "L'URL cible ne contient pas de nom d'hôte valide.",
        )

    if parts.username or parts.password:
        raise PerformanceServiceError(
            "INVALID_TARGET_URL",
            "Les identifiants ne doivent pas être placés dans l'URL cible.",
        )

    try:
        port = parts.port
    except ValueError as error:
        raise PerformanceServiceError(
            "INVALID_TARGET_URL",
            "Le port de l'URL cible est invalide.",
        ) from error

    allowed_ports = set(current_app.config.get("PERFORMANCE_ALLOWED_TARGET_PORTS") or [])
    if port is not None and allowed_ports and port not in allowed_ports:
        raise PerformanceServiceError(
            "TARGET_PORT_NOT_ALLOWED",
            f"Le port {port} n'est pas autorisé pour les tests de performance.",
            403,
        )

    _validate_target_allowlist(parts.hostname.lower())

    # Les fragments n'ont aucun effet côté HTTP et sont retirés pour éviter
    # d'enregistrer deux cibles différentes qui représentent la même requête.
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))


def _validate_target_allowlist(hostname: str) -> None:
    require_allowlist = bool(
        current_app.config.get("PERFORMANCE_REQUIRE_TARGET_ALLOWLIST", True)
    )
    allowed = [
        str(item).strip().lower()
        for item in (current_app.config.get("PERFORMANCE_ALLOWED_TARGETS") or [])
        if str(item).strip()
    ]

    if not require_allowlist:
        return

    if not allowed:
        raise PerformanceServiceError(
            "PERFORMANCE_TARGET_ALLOWLIST_EMPTY",
            "Aucune cible k6 n'est autorisée. Configurez PERFORMANCE_ALLOWED_TARGETS côté serveur.",
            503,
        )

    def matches(rule: str) -> bool:
        if rule.startswith("*."):
            suffix = rule[1:]
            return hostname.endswith(suffix) and hostname != suffix.lstrip(".")
        if rule.startswith("."):
            base = rule[1:]
            return hostname == base or hostname.endswith(rule)
        return hostname == rule

    if not any(matches(rule) for rule in allowed):
        raise PerformanceServiceError(
            "TARGET_NOT_ALLOWED",
            "Cette cible n'est pas autorisée pour les tests de performance.",
            403,
        )


def _validate_namespace(value: Any) -> str:
    namespace = str(value or "").strip().lower()
    if not namespace:
        raise PerformanceServiceError(
            "OBSERVABILITY_NAMESPACE_REQUIRED",
            "Le namespace Kubernetes d'observabilité est obligatoire.",
        )

    if len(namespace) > 63 or not DNS_LABEL_RE.fullmatch(namespace):
        raise PerformanceServiceError(
            "INVALID_OBSERVABILITY_NAMESPACE",
            "Le namespace Kubernetes doit être un label DNS-1123 valide.",
        )
    return namespace


def _validate_hostname(value: Any) -> str | None:
    hostname = str(value or "").strip().lower()
    if not hostname:
        return None

    if not DNS_HOST_RE.fullmatch(hostname):
        raise PerformanceServiceError(
            "INVALID_GRAFANA_HOST",
            "Le hostname Grafana est invalide.",
        )
    return hostname


def _validate_observability(mode: str, raw: Any) -> dict[str, Any] | None:
    if mode == "basic":
        return None

    if not isinstance(raw, dict):
        raise PerformanceServiceError(
            "OBSERVABILITY_CONFIGURATION_REQUIRED",
            "La configuration Prometheus/Grafana est obligatoire en mode observability.",
        )

    install_prometheus = bool(raw.get("installPrometheus", True))
    install_grafana = bool(raw.get("installGrafana", True))

    # La V1 ne reçoit pas une URL Prometheus arbitraire depuis le navigateur.
    # Le service Prometheus attendu sera provisionné dans le namespace fourni.
    if not install_prometheus:
        raise PerformanceServiceError(
            "PROMETHEUS_REQUIRED",
            "Le mode observability nécessite Prometheus dans cette version.",
        )

    return {
        "namespace": _validate_namespace(raw.get("namespace")),
        "retentionDays": _integer(
            raw.get("retentionDays", 7),
            field="retentionDays",
            minimum=1,
            maximum=int(current_app.config.get("PERFORMANCE_MAX_RETENTION_DAYS", 90)),
        ),
        "grafanaIngressHost": _validate_hostname(raw.get("grafanaIngressHost")),
        "installPrometheus": install_prometheus,
        "installGrafana": install_grafana,
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
        "observability": _validate_observability(mode, payload.get("observability")),
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
    result = serialize_run_summary(row)
    result.update(
        {
            "thresholds": (
                row.get("threshold_results")
                if isinstance(row.get("threshold_results"), list)
                else _empty_threshold_results(row.get("thresholds"))
            ),
            "observability": row.get("observability"),
            "logs": [
                {
                    "id": int(log["id"]),
                    "createdAt": _iso(log.get("created_at")),
                    "level": log.get("level") or "info",
                    "message": log.get("message") or "",
                }
                for log in repository.list_run_logs(int(row["id"]))
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


def create_and_run(payload: dict[str, Any], created_by: int) -> dict[str, Any]:
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


def get_performance_overview(owner_user_id: int | None) -> dict[str, int]:
    values = repository.get_overview(owner_user_id)
    return {
        "totalTests": values["total_tests"],
        "totalRuns": values["total_runs"],
        "runningRuns": values["running_runs"],
        "passedRuns": values["passed_runs"],
        "failedRuns": values["failed_runs"],
    }
