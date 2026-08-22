from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from app.integrations.security import decrypt_credential


PROMETHEUS_CHART_VERSION = "29.21.0"
GRAFANA_CHART_VERSION = "12.10.0"
PROMETHEUS_SERVICE_NAME = "sapixi-k6-prometheus"
GRAFANA_SERVICE_NAME = "sapixi-k6-grafana"
DASHBOARD_UID = "k6-performance"


class ObservabilityProvisionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ObservabilityProvisionResult:
    prometheus_remote_write_url: str
    prometheus_query_url: str
    grafana_base_url: str | None


def _require_binary(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise ObservabilityProvisionError(
            "OBSERVABILITY_TOOL_MISSING",
            f"L'outil '{name}' est introuvable dans le worker observability.",
        )
    return path


def _dashboard_json() -> str:
    path = (
        Path(__file__).resolve().parents[2]
        / "ansible"
        / "observability"
        / "files"
        / "k6-dashboard.json"
    )
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise ObservabilityProvisionError(
            "OBSERVABILITY_DASHBOARD_MISSING",
            "Le dashboard Grafana k6 est introuvable dans le backend.",
        ) from error


def _write_kubeconfig(path: Path, stack: dict[str, Any]) -> None:
    server = str(stack.get("kubernetes_base_url") or "").strip().rstrip("/")
    token = decrypt_credential(stack.get("kubernetes_secret_ciphertext"))
    if not server or not token:
        raise ObservabilityProvisionError(
            "KUBERNETES_CREDENTIAL_REQUIRED",
            "La connexion Kubernetes ne contient pas l'URL API et le token nécessaires.",
        )

    cluster: dict[str, Any] = {"server": server}
    if not bool(stack.get("kubernetes_verify_ssl", True)):
        cluster["insecure-skip-tls-verify"] = True

    kubeconfig = {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [
            {
                "name": "sapixi-target",
                "cluster": cluster,
            }
        ],
        "users": [
            {
                "name": "sapixi-user",
                "user": {"token": token},
            }
        ],
        "contexts": [
            {
                "name": "sapixi-context",
                "context": {
                    "cluster": "sapixi-target",
                    "user": "sapixi-user",
                    "namespace": str(stack["namespace"]),
                },
            }
        ],
        "current-context": "sapixi-context",
    }
    path.write_text(yaml.safe_dump(kubeconfig, sort_keys=False), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _run_checked(command: list[str], *, timeout: int = 30) -> str:
    process = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        shell=False,
        check=False,
    )
    output = (process.stdout or "").strip()
    if process.returncode != 0:
        raise ObservabilityProvisionError(
            "KUBERNETES_PREFLIGHT_FAILED",
            output or f"La commande {' '.join(command[:3])} a échoué.",
        )
    return output


def _check_namespace_and_rbac(kubectl: str, kubeconfig: Path, stack: dict[str, Any]) -> None:
    namespace = str(stack["namespace"])
    base = [kubectl, "--kubeconfig", str(kubeconfig)]

    # Do not require cluster-scoped permission to read Namespace objects.
    # A namespace-scoped provisioning identity is enough: listing ConfigMaps
    # both proves that the namespace exists and that the token can reach it.
    try:
        _run_checked(
            base + ["get", "configmaps", "--namespace", namespace, "--request-timeout=10s"],
            timeout=20,
        )
    except ObservabilityProvisionError as error:
        raise ObservabilityProvisionError(
            "KUBERNETES_NAMESPACE_UNAVAILABLE",
            "Le namespace est introuvable ou la connexion Kubernetes n'a pas le droit d'y accéder. "
            f"Détail : {error.message}",
        ) from error

    resources = [
        "deployments.apps",
        "replicasets.apps",
        "pods",
        "services",
        "configmaps",
        "secrets",
        "persistentvolumeclaims",
    ]
    if bool(stack.get("ingress_enabled")):
        resources.append("ingresses.networking.k8s.io")

    denied: list[str] = []
    verbs = ("get", "list", "watch", "create", "update", "patch", "delete")
    for resource in resources:
        for verb in verbs:
            output = _run_checked(
                base
                + [
                    "auth",
                    "can-i",
                    verb,
                    resource,
                    "--namespace",
                    namespace,
                ]
            )
            if output.strip().lower() != "yes":
                denied.append(f"{verb} {resource}")

    if denied:
        preview = ", ".join(sorted(set(denied))[:12])
        if len(set(denied)) > 12:
            preview += ", …"
        raise ObservabilityProvisionError(
            "KUBERNETES_RBAC_INSUFFICIENT",
            "Droits Kubernetes insuffisants dans le namespace "
            f"{namespace} : {preview}.",
        )


def _prometheus_values(stack: dict[str, Any]) -> dict[str, Any]:
    persistent: dict[str, Any] = {
        "enabled": True,
        "size": str(stack.get("prometheus_storage_size") or "8Gi"),
        "accessModes": ["ReadWriteOnce"],
    }
    storage_class = str(stack.get("storage_class_name") or "").strip()
    if storage_class:
        persistent["storageClass"] = storage_class

    return {
        # We use this Prometheus only as a k6 remote-write receiver.
        # Disable the chart's Kubernetes discovery scrape jobs so the Prometheus
        # pod itself does not need cluster-wide discovery RBAC.
        "scrapeConfigs": None,
        "alertmanager": {"enabled": False},
        "kube-state-metrics": {"enabled": False},
        "prometheus-node-exporter": {"enabled": False},
        "prometheus-pushgateway": {"enabled": False},
        "rbac": {"create": False},
        "serviceAccounts": {"server": {"create": False}},
        "server": {
            "fullnameOverride": PROMETHEUS_SERVICE_NAME,
            "retention": f"{int(stack.get('retention_days') or 7)}d",
            "extraFlags": ["web.enable-remote-write-receiver"],
            "persistentVolume": persistent,
            "service": {
                "enabled": True,
                "type": "ClusterIP",
                "servicePort": 9090,
            },
            "serverFiles": {
                "prometheus.yml": {
                    "scrape_configs": [],
                }
            },
            "resources": {
                "requests": {"cpu": "100m", "memory": "256Mi"},
                "limits": {"cpu": "1000m", "memory": "1Gi"},
            },
        },
    }


def _grafana_values(stack: dict[str, Any], admin_password: str) -> dict[str, Any]:
    namespace = str(stack["namespace"])
    prometheus_url = (
        f"http://{PROMETHEUS_SERVICE_NAME}.{namespace}.svc.cluster.local:9090"
    )
    persistence: dict[str, Any] = {
        "enabled": True,
        "type": "pvc",
        "size": str(stack.get("grafana_storage_size") or "2Gi"),
        "accessModes": ["ReadWriteOnce"],
    }
    storage_class = str(stack.get("storage_class_name") or "").strip()
    if storage_class:
        persistence["storageClassName"] = storage_class

    ingress_enabled = bool(stack.get("ingress_enabled"))
    ingress: dict[str, Any] = {"enabled": ingress_enabled}
    if ingress_enabled:
        host = str(stack.get("grafana_host") or "")
        ingress.update(
            {
                "hosts": [host],
                "path": "/",
                "pathType": "Prefix",
            }
        )
        ingress_class = str(stack.get("ingress_class_name") or "").strip()
        if ingress_class:
            ingress["ingressClassName"] = ingress_class
        if bool(stack.get("grafana_tls_enabled")):
            ingress["tls"] = [
                {
                    "secretName": str(stack.get("grafana_tls_secret_name")),
                    "hosts": [host],
                }
            ]

    return {
        "fullnameOverride": GRAFANA_SERVICE_NAME,
        "rbac": {"create": False},
        "serviceAccount": {"create": False},
        "adminUser": str(stack.get("grafana_admin_user") or "admin"),
        "adminPassword": admin_password,
        "persistence": persistence,
        "service": {"type": "ClusterIP", "port": 80},
        "ingress": ingress,
        "datasources": {
            "datasources.yaml": {
                "apiVersion": 1,
                "datasources": [
                    {
                        "name": "Prometheus",
                        "uid": "prometheus",
                        "type": "prometheus",
                        "access": "proxy",
                        "url": prometheus_url,
                        "isDefault": True,
                        "editable": False,
                    }
                ],
            }
        },
        "dashboardProviders": {
            "dashboardproviders.yaml": {
                "apiVersion": 1,
                "providers": [
                    {
                        "name": "sapixi-k6",
                        "orgId": 1,
                        "folder": "Sapixi Performance",
                        "type": "file",
                        "disableDeletion": True,
                        "editable": False,
                        "options": {
                            "path": "/var/lib/grafana/dashboards/sapixi-k6"
                        },
                    }
                ],
            }
        },
        "dashboards": {
            "sapixi-k6": {
                "k6-performance": {
                    "json": _dashboard_json(),
                }
            }
        },
        "resources": {
            "requests": {"cpu": "100m", "memory": "128Mi"},
            "limits": {"cpu": "500m", "memory": "512Mi"},
        },
    }


def _run_ansible(
    ansible_playbook: str,
    playbook_path: Path,
    extra_vars_path: Path,
    *,
    heartbeat: Callable[[], None],
    log_line: Callable[[str], None],
) -> None:
    process = subprocess.Popen(
        [
            ansible_playbook,
            str(playbook_path),
            "--extra-vars",
            f"@{extra_vars_path}",
        ],
        cwd=str(playbook_path.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        shell=False,
    )

    output_queue: queue.Queue[str | None] = queue.Queue()

    def reader() -> None:
        try:
            if process.stdout is not None:
                for raw_line in process.stdout:
                    output_queue.put(raw_line)
        finally:
            output_queue.put(None)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    last_heartbeat = 0.0

    while process.poll() is None:
        now = time.monotonic()
        if now - last_heartbeat >= 5:
            heartbeat()
            last_heartbeat = now
        try:
            line = output_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        if line is not None:
            cleaned = line.strip()
            if cleaned:
                log_line(cleaned[:2000])

    while True:
        try:
            line = output_queue.get_nowait()
        except queue.Empty:
            break
        if line is not None and line.strip():
            log_line(line.strip()[:2000])

    if process.returncode != 0:
        raise ObservabilityProvisionError(
            "ANSIBLE_PROVISIONING_FAILED",
            f"ansible-playbook s'est terminé avec le code {process.returncode}.",
        )


def provision_observability_stack(
    stack: dict[str, Any],
    *,
    heartbeat: Callable[[], None],
    log_line: Callable[[str], None],
) -> ObservabilityProvisionResult:
    ansible_playbook = _require_binary("ansible-playbook")
    helm = _require_binary("helm")
    kubectl = _require_binary("kubectl")

    if not stack.get("kubernetes_enabled"):
        raise ObservabilityProvisionError(
            "KUBERNETES_CONNECTION_DISABLED",
            "La connexion Kubernetes sélectionnée est désactivée.",
        )

    admin_password = decrypt_credential(
        stack.get("grafana_admin_password_ciphertext")
    )
    if not admin_password:
        raise ObservabilityProvisionError(
            "GRAFANA_CREDENTIAL_UNAVAILABLE",
            "Le mot de passe administrateur Grafana n'a pas pu être déchiffré.",
        )

    api_root = Path(__file__).resolve().parents[2]
    playbook_path = api_root / "ansible" / "observability" / "playbook.yml"
    if not playbook_path.exists():
        raise ObservabilityProvisionError(
            "ANSIBLE_PLAYBOOK_MISSING",
            "Le playbook Ansible observability est introuvable.",
        )

    with tempfile.TemporaryDirectory(prefix="sapixi-observability-") as temp_dir:
        temp = Path(temp_dir)
        kubeconfig_path = temp / "kubeconfig.yml"
        extra_vars_path = temp / "vars.yml"
        _write_kubeconfig(kubeconfig_path, stack)

        log_line("Vérification du namespace Kubernetes et des droits RBAC.")
        _check_namespace_and_rbac(kubectl, kubeconfig_path, stack)
        heartbeat()

        namespace = str(stack["namespace"])
        variables = {
            "kubeconfig_path": str(kubeconfig_path),
            "namespace": namespace,
            "prometheus_release_name": str(
                stack.get("prometheus_release_name") or "sapixi-k6-prometheus"
            ),
            "grafana_release_name": str(
                stack.get("grafana_release_name") or "sapixi-k6-grafana"
            ),
            "prometheus_chart_version": PROMETHEUS_CHART_VERSION,
            "grafana_chart_version": GRAFANA_CHART_VERSION,
            "prometheus_service_name": PROMETHEUS_SERVICE_NAME,
            "grafana_service_name": GRAFANA_SERVICE_NAME,
            "prometheus_values": _prometheus_values(stack),
            "grafana_values": _grafana_values(stack, admin_password),
            # Useful for diagnostics without exposing credentials.
            "helm_binary": helm,
        }
        extra_vars_path.write_text(
            yaml.safe_dump(variables, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        try:
            os.chmod(extra_vars_path, 0o600)
        except OSError:
            pass

        log_line(
            "Installation/upgrade Prometheus puis Grafana via Helm et Ansible."
        )
        _run_ansible(
            ansible_playbook,
            playbook_path,
            extra_vars_path,
            heartbeat=heartbeat,
            log_line=log_line,
        )

    prometheus_query_url = (
        f"http://{PROMETHEUS_SERVICE_NAME}.{stack['namespace']}.svc.cluster.local:9090"
    )
    prometheus_remote_write_url = f"{prometheus_query_url}/api/v1/write"

    grafana_base_url: str | None = None
    if bool(stack.get("ingress_enabled")) and stack.get("grafana_host"):
        scheme = "https" if bool(stack.get("grafana_tls_enabled")) else "http"
        grafana_base_url = f"{scheme}://{stack['grafana_host']}"

    return ObservabilityProvisionResult(
        prometheus_remote_write_url=prometheus_remote_write_url,
        prometheus_query_url=prometheus_query_url,
        grafana_base_url=grafana_base_url,
    )
