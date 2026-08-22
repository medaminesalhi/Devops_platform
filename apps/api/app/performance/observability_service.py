from __future__ import annotations

import re
import secrets
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from app.integrations.repository import find_connection
from app.integrations.security import decrypt_credential, encrypt_credential
from app.performance import observability_repository
from app.performance.service import PerformanceServiceError


DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
DNS_HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)*"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$"
)
K8S_QUANTITY_RE = re.compile(r"^[1-9][0-9]*(?:Ki|Mi|Gi|Ti)$")


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _positive_int(value: Any, *, field: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise PerformanceServiceError(
            "INVALID_OBSERVABILITY_CONFIGURATION",
            f"Le champ {field} doit être un entier.",
        ) from error
    if parsed < minimum or parsed > maximum:
        raise PerformanceServiceError(
            "INVALID_OBSERVABILITY_CONFIGURATION",
            f"Le champ {field} doit être compris entre {minimum} et {maximum}.",
        )
    return parsed


def _namespace(value: Any) -> str:
    namespace = str(value or "").strip().lower()
    if not namespace or not DNS_LABEL_RE.fullmatch(namespace):
        raise PerformanceServiceError(
            "INVALID_OBSERVABILITY_NAMESPACE",
            "Le namespace Kubernetes doit être un label DNS-1123 valide.",
        )
    return namespace


def _storage_size(value: Any, *, field: str, default: str) -> str:
    normalized = str(value or default).strip()
    if not K8S_QUANTITY_RE.fullmatch(normalized):
        raise PerformanceServiceError(
            "INVALID_OBSERVABILITY_STORAGE",
            f"{field} doit utiliser une quantité Kubernetes, par exemple 2Gi ou 10Gi.",
        )
    return normalized


def _optional_dns_label(value: Any, *, field: str) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    if not DNS_LABEL_RE.fullmatch(normalized):
        raise PerformanceServiceError(
            "INVALID_OBSERVABILITY_CONFIGURATION",
            f"{field} est invalide.",
        )
    return normalized


def _optional_host(value: Any) -> str | None:
    host = str(value or "").strip().lower()
    if not host:
        return None
    if "://" in host:
        parts = urlsplit(host)
        host = (parts.hostname or "").strip().lower()
    if not host or not DNS_HOST_RE.fullmatch(host):
        raise PerformanceServiceError(
            "INVALID_GRAFANA_HOST",
            "Le hostname Grafana est invalide.",
        )
    return host


def _validate_kubernetes_connection(connection_id: int) -> dict[str, Any]:
    connection = find_connection(connection_id)
    if connection is None:
        raise PerformanceServiceError(
            "KUBERNETES_CONNECTION_NOT_FOUND",
            "La connexion Kubernetes est introuvable.",
            404,
        )
    if connection.get("provider_type") != "kubernetes":
        raise PerformanceServiceError(
            "INVALID_KUBERNETES_CONNECTION",
            "La connexion sélectionnée n'est pas de type Kubernetes.",
        )
    if not connection.get("enabled"):
        raise PerformanceServiceError(
            "KUBERNETES_CONNECTION_DISABLED",
            "La connexion Kubernetes est désactivée.",
            409,
        )
    if connection.get("auth_type") != "token" or not connection.get("secret_ciphertext"):
        raise PerformanceServiceError(
            "KUBERNETES_CREDENTIAL_REQUIRED",
            "La connexion Kubernetes doit disposer d'un token.",
            409,
        )
    if connection.get("status") == "offline":
        raise PerformanceServiceError(
            "KUBERNETES_CONNECTION_OFFLINE",
            "La connexion Kubernetes est actuellement hors ligne.",
            409,
        )
    return connection


def serialize_stack(row: dict[str, Any], *, include_logs: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": int(row["id"]),
        "projectId": int(row["project_id"]),
        "projectName": row.get("project_name") or f"Projet #{row['project_id']}",
        "kubernetesConnectionId": int(row["kubernetes_connection_id"]),
        "kubernetesConnectionName": row.get("kubernetes_connection_name") or "Kubernetes",
        "namespace": row.get("namespace") or "",
        "status": row.get("status") or "queued",
        "retentionDays": int(row.get("retention_days") or 7),
        "prometheusStorageSize": row.get("prometheus_storage_size") or "8Gi",
        "grafanaStorageSize": row.get("grafana_storage_size") or "2Gi",
        "storageClassName": row.get("storage_class_name"),
        "ingressEnabled": bool(row.get("ingress_enabled")),
        "ingressClassName": row.get("ingress_class_name"),
        "grafanaHost": row.get("grafana_host"),
        "grafanaTlsEnabled": bool(row.get("grafana_tls_enabled")),
        "grafanaTlsSecretName": row.get("grafana_tls_secret_name"),
        "prometheusRemoteWriteUrl": row.get("prometheus_remote_write_url"),
        "prometheusQueryUrl": row.get("prometheus_query_url"),
        "grafanaBaseUrl": row.get("grafana_base_url"),
        "grafanaDashboardUid": row.get("grafana_dashboard_uid") or "k6-performance",
        "grafanaAdminUser": row.get("grafana_admin_user") or "admin",
        "credentialsConfigured": bool(row.get("grafana_admin_password_ciphertext")),
        "errorCode": row.get("error_code"),
        "errorMessage": row.get("error_message"),
        "createdAt": _iso(row.get("created_at")),
        "startedAt": _iso(row.get("started_at")),
        "finishedAt": _iso(row.get("finished_at")),
    }
    if include_logs:
        result["logs"] = [
            {
                "id": int(log["id"]),
                "level": log.get("level") or "info",
                "message": log.get("message") or "",
                "createdAt": _iso(log.get("created_at")),
            }
            for log in observability_repository.list_logs(int(row["id"]))
        ]
    return result


def create_observability_stack(payload: dict[str, Any], created_by: int) -> dict[str, Any]:
    try:
        project_id = int(payload.get("projectId"))
        connection_id = int(payload.get("kubernetesConnectionId"))
    except (TypeError, ValueError) as error:
        raise PerformanceServiceError(
            "INVALID_OBSERVABILITY_CONFIGURATION",
            "Le projet ou la connexion Kubernetes est invalide.",
        ) from error

    if project_id <= 0 or connection_id <= 0:
        raise PerformanceServiceError(
            "INVALID_OBSERVABILITY_CONFIGURATION",
            "Le projet et la connexion Kubernetes sont obligatoires.",
        )

    _validate_kubernetes_connection(connection_id)

    namespace = _namespace(payload.get("namespace"))
    retention_days = _positive_int(
        payload.get("retentionDays", 7),
        field="retentionDays",
        minimum=1,
        maximum=365,
    )
    ingress_enabled = bool(payload.get("ingressEnabled", False))
    grafana_host = _optional_host(payload.get("grafanaHost"))
    ingress_class_name = _optional_dns_label(
        payload.get("ingressClassName"),
        field="ingressClassName",
    )
    tls_enabled = bool(payload.get("grafanaTlsEnabled", False))
    tls_secret = _optional_dns_label(
        payload.get("grafanaTlsSecretName"),
        field="grafanaTlsSecretName",
    )

    if ingress_enabled and not grafana_host:
        raise PerformanceServiceError(
            "GRAFANA_HOST_REQUIRED",
            "Un hostname Grafana est obligatoire lorsque l'Ingress est activé.",
        )
    if tls_enabled and (not ingress_enabled or not tls_secret):
        raise PerformanceServiceError(
            "GRAFANA_TLS_SECRET_REQUIRED",
            "Le TLS Grafana nécessite un Ingress et le nom d'un Secret TLS existant.",
        )

    password = secrets.token_urlsafe(24)
    encrypted_password = encrypt_credential(password)
    if not encrypted_password:
        raise RuntimeError("Impossible de chiffrer le mot de passe Grafana.")

    try:
        row = observability_repository.create_stack(
            project_id=project_id,
            created_by=created_by,
            kubernetes_connection_id=connection_id,
            namespace=namespace,
            retention_days=retention_days,
            prometheus_storage_size=_storage_size(
                payload.get("prometheusStorageSize"),
                field="prometheusStorageSize",
                default="8Gi",
            ),
            grafana_storage_size=_storage_size(
                payload.get("grafanaStorageSize"),
                field="grafanaStorageSize",
                default="2Gi",
            ),
            storage_class_name=(str(payload.get("storageClassName") or "").strip() or None),
            ingress_enabled=ingress_enabled,
            ingress_class_name=ingress_class_name,
            grafana_host=grafana_host,
            grafana_tls_enabled=tls_enabled,
            grafana_tls_secret_name=tls_secret,
            grafana_admin_password_ciphertext=encrypted_password,
        )
    except Exception as error:
        # PostgreSQL unique violation is intentionally mapped to a friendly conflict.
        if getattr(error, "sqlstate", None) == "23505":
            raise PerformanceServiceError(
                "OBSERVABILITY_STACK_ALREADY_EXISTS",
                "Une stack Prometheus/Grafana existe déjà pour ce projet et ce namespace.",
                409,
            ) from error
        raise

    return serialize_stack(row, include_logs=True)


def list_observability_stacks(
    *,
    owner_user_id: int | None,
    project_id: int | None,
) -> list[dict[str, Any]]:
    return [
        serialize_stack(row)
        for row in observability_repository.list_stacks(
            owner_user_id=owner_user_id,
            project_id=project_id,
        )
    ]


def get_observability_stack(
    stack_id: int,
    *,
    owner_user_id: int | None,
) -> dict[str, Any]:
    row = observability_repository.find_stack(stack_id, owner_user_id=owner_user_id)
    if row is None:
        raise PerformanceServiceError(
            "OBSERVABILITY_STACK_NOT_FOUND",
            "La stack Prometheus/Grafana est introuvable.",
            404,
        )
    return serialize_stack(row, include_logs=True)


def retry_observability_stack(
    stack_id: int,
    *,
    owner_user_id: int | None,
) -> dict[str, Any]:
    row = observability_repository.find_stack(stack_id, owner_user_id=owner_user_id)
    if row is None:
        raise PerformanceServiceError(
            "OBSERVABILITY_STACK_NOT_FOUND",
            "La stack Prometheus/Grafana est introuvable.",
            404,
        )
    if row.get("status") != "failed":
        raise PerformanceServiceError(
            "OBSERVABILITY_STACK_NOT_FAILED",
            "Seule une installation en échec peut être relancée.",
            409,
        )
    if not observability_repository.requeue_stack(stack_id):
        raise PerformanceServiceError(
            "OBSERVABILITY_STACK_RETRY_FAILED",
            "Impossible de remettre cette installation dans la file.",
            409,
        )
    refreshed = observability_repository.find_stack(stack_id, owner_user_id=owner_user_id)
    assert refreshed is not None
    observability_repository.add_log(
        stack_id,
        level="info",
        message="Provisioning Prometheus/Grafana relancé.",
    )
    return serialize_stack(refreshed, include_logs=True)


def get_grafana_credentials(
    stack_id: int,
    *,
    owner_user_id: int | None,
) -> dict[str, Any]:
    row = observability_repository.find_stack(stack_id, owner_user_id=owner_user_id)
    if row is None:
        raise PerformanceServiceError(
            "OBSERVABILITY_STACK_NOT_FOUND",
            "La stack Prometheus/Grafana est introuvable.",
            404,
        )
    if row.get("status") != "ready":
        raise PerformanceServiceError(
            "OBSERVABILITY_STACK_NOT_READY",
            "Grafana n'est pas encore prêt.",
            409,
        )
    password = decrypt_credential(row.get("grafana_admin_password_ciphertext"))
    if not password:
        raise PerformanceServiceError(
            "GRAFANA_CREDENTIAL_UNAVAILABLE",
            "Le mot de passe Grafana n'est pas disponible.",
            409,
        )
    return {
        "username": row.get("grafana_admin_user") or "admin",
        "password": password,
        "grafanaBaseUrl": row.get("grafana_base_url"),
    }


def resolve_stack_observability_config(
    stack_id: int,
    *,
    owner_user_id: int | None,
    project_id: int,
) -> dict[str, Any]:
    row = observability_repository.find_ready_stack(
        stack_id,
        owner_user_id=owner_user_id,
    )
    if row is None or int(row.get("project_id") or 0) != project_id:
        raise PerformanceServiceError(
            "OBSERVABILITY_STACK_NOT_READY",
            "La stack Prometheus/Grafana sélectionnée n'est pas prête.",
            409,
        )
    remote_write = str(row.get("prometheus_remote_write_url") or "").strip()
    if not remote_write:
        raise PerformanceServiceError(
            "PROMETHEUS_REMOTE_WRITE_URL_MISSING",
            "L'URL Remote Write de cette stack est absente.",
            409,
        )
    return {
        "stackId": int(row["id"]),
        "namespace": row.get("namespace"),
        "retentionDays": int(row.get("retention_days") or 7),
        "prometheusRemoteWriteUrl": remote_write,
        "grafanaBaseUrl": row.get("grafana_base_url"),
        "grafanaDashboardUid": row.get("grafana_dashboard_uid") or "k6-performance",
    }
