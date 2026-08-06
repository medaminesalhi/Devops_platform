from __future__ import annotations

import re
from datetime import datetime, timezone
from collections import OrderedDict
from typing import Any
from urllib.parse import urlparse

from app.deployments import repository
from app.deployments.diagnostics import chat_with_ai, diagnose_with_ai


ALLOWED_DEPLOYMENT_STATUSES = {
    "draft",
    "ready",
    "queued",
    "running",
    "waiting_confirmation",
    "succeeded",
    "failed",
    "cancelled",
}

ALLOWED_SYNC_MODES = {
    "prepare_only",
    "confirm_before_sync",
    "automatic",
}

REQUIRED_SERVICE_ROLES = {
    "kubernetes": "Kubernetes",
    "argocd": "Argo CD",
    "container_registry": "Registre Nexus",
    "gitops_repository": "Repository GitOps",
}

STAGE_LABELS = {
    "prepare": "Préparation",
    "source": "Récupération du code",
    "build": "Build Docker",
    "registry": "Publication Nexus",
    "gitops": "Publication GitOps",
    "argocd": "Synchronisation Argo CD",
    "kubernetes": "Vérification Kubernetes",
    "health": "Vérification de santé",
}


class DeploymentServiceError(RuntimeError):
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
    return value.isoformat() if value is not None else None


def _safe_int(value: Any, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise DeploymentServiceError(
            "INVALID_IDENTIFIER",
            f"Le champ {field_name} est invalide.",
        ) from error
    if parsed <= 0:
        raise DeploymentServiceError(
            "INVALID_IDENTIFIER",
            f"Le champ {field_name} est invalide.",
        )
    return parsed


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized or "application"


def _registry_host(base_url: str) -> str:
    parsed = urlparse(base_url)
    value = parsed.netloc or parsed.path
    return value.strip().rstrip("/")


def _summary_json(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "projectId": int(row["project_id"]),
        "projectName": row.get("project_name") or "Projet",
        "projectSlug": row.get("project_slug") or "project",
        "generationId": int(row["generation_run_id"] or 0),
        "environmentId": int(row["environment_id"] or 0),
        "environmentName": row.get("environment_name") or row.get("environment") or "—",
        "namespace": row.get("namespace") or "default",
        "version": row.get("version") or row.get("image_tag") or "—",
        "sourceCommit": row.get("source_commit") or row.get("commit_sha") or "",
        "gitopsCommit": row.get("gitops_commit"),
        "status": row.get("status") or "draft",
        "currentStage": row.get("current_stage"),
        "currentStageLabel": row.get("current_stage_label") or "Brouillon",
        "progress": int(row.get("progress") or 0),
        "createdByName": row.get("created_by_name") or "Système",
        "createdAt": _iso(row.get("created_at")) or "",
        "startedAt": _iso(row.get("started_at")),
        "finishedAt": _iso(row.get("finished_at")),
        "note": row.get("note"),
    }


def _step_json(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("code") or str(row["id"]),
        "stage": row.get("stage") or row.get("code") or "prepare",
        "order": int(row.get("step_order") or 0),
        "label": row.get("name") or "Étape",
        "description": row.get("description") or "",
        "status": row.get("status") or "pending",
        "startedAt": _iso(row.get("started_at")),
        "finishedAt": _iso(row.get("finished_at")),
        "durationSeconds": row.get("duration_seconds"),
        "details": row.get("details") or {},
        "errorCode": row.get("error_code"),
        "errorMessage": row.get("error_message") or row.get("message"),
    }


def _log_json(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "createdAt": _iso(row.get("created_at")) or "",
        "scope": row.get("scope") or "system",
        "level": row.get("level") or "info",
        "stepId": row.get("step_code"),
        "componentName": row.get("component_name"),
        "message": row.get("message") or "",
    }


def _component_json(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("component_key") or str(row["id"]),
        "name": row.get("name") or "Composant",
        "type": row.get("component_type") or "application",
        "imageRepository": row.get("image_repository") or "",
        "imageTag": row.get("image_tag") or "",
        "imageDigest": row.get("image_digest"),
        "port": row.get("port"),
        "replicas": int(row.get("replicas") or 1),
        "buildStatus": row.get("build_status") or "pending",
        "registryStatus": row.get("registry_status") or "pending",
    }


def _resource_json(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("resource_key") or str(row["id"]),
        "kind": row.get("kind") or "pod",
        "name": row.get("name") or "Ressource",
        "namespace": row.get("namespace") or "default",
        "status": row.get("status") or "Unknown",
        "health": row.get("health") or "unknown",
        "ready": row.get("ready"),
        "image": row.get("image"),
        "restarts": row.get("restarts"),
        "age": row.get("age") or "—",
        "message": row.get("message"),
        "url": row.get("url"),
    }


def _incident_json(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "code": row.get("code") or "DEPLOYMENT_FAILED",
        "title": row.get("title") or "Déploiement échoué",
        "message": row.get("message") or "",
        "stage": row.get("stage") or "prepare",
        "stepId": row.get("step_code") or row.get("stage") or "prepare",
        "componentName": row.get("component_name"),
        "integrationName": row.get("integration_name"),
        "occurredAt": _iso(row.get("occurred_at")) or "",
        "retryable": bool(row.get("retryable")),
        "requiresNewGeneration": bool(row.get("requires_new_generation")),
    }


def _correction_json(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "title": row.get("title") or "Correction proposée",
        "summary": row.get("summary") or "",
        "targetPhase": row.get("target_phase") or "deployment",
        "targetFile": row.get("target_file"),
        "diff": row.get("diff"),
        "risk": row.get("risk") or "medium",
        "status": row.get("status") or "proposed",
    }


def _diagnostic_json(
    row: dict[str, Any] | None,
    corrections: list[dict[str, Any]],
) -> dict[str, Any]:
    if row is None:
        return {
            "status": "idle",
            "cause": None,
            "explanation": None,
            "confidence": None,
            "targetPhase": None,
            "evidence": [],
            "corrections": [],
            "createdAt": None,
        }
    return {
        "status": row.get("status") or "idle",
        "cause": row.get("cause"),
        "explanation": row.get("explanation"),
        "confidence": row.get("confidence"),
        "targetPhase": row.get("target_phase"),
        "evidence": row.get("evidence") or [],
        "corrections": [_correction_json(item) for item in corrections],
        "createdAt": _iso(row.get("created_at")),
    }


def _chat_json(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "role": row.get("role") or "assistant",
        "content": row.get("content") or "",
        "createdAt": _iso(row.get("created_at")) or "",
    }


def _health_json(resources: list[dict[str, Any]]) -> dict[str, Any]:
    argocd = next(
        (item for item in resources if item.get("kind") == "argocd_application"),
        None,
    )
    pods = [item for item in resources if item.get("kind") == "pod"]
    ready_pods = sum(
        1
        for item in pods
        if item.get("health") == "healthy"
        and str(item.get("ready") or "").split("/")[0]
        == str(item.get("ready") or "").split("/")[-1]
    )
    ingress = next(
        (item for item in resources if item.get("kind") == "ingress"),
        None,
    )
    jobs = [item for item in resources if item.get("kind") == "job"]
    migration_status = "not_required"
    if jobs:
        migration_status = (
            "failed"
            if any(item.get("health") == "degraded" for item in jobs)
            else "succeeded"
            if all(item.get("health") == "healthy" for item in jobs)
            else "pending"
        )
    return {
        "argocdSync": (
            "Synced"
            if argocd and argocd.get("status") == "Synced"
            else "OutOfSync"
            if argocd and argocd.get("status") == "OutOfSync"
            else "Unknown"
        ),
        "argocdHealth": (
            "Healthy"
            if argocd and argocd.get("health") == "healthy"
            else "Progressing"
            if argocd and argocd.get("health") == "progressing"
            else "Degraded"
            if argocd and argocd.get("health") == "degraded"
            else "Unknown"
        ),
        "readyPods": ready_pods,
        "totalPods": len(pods),
        "ingressReady": bool(ingress and ingress.get("health") == "healthy"),
        "migrationStatus": migration_status,
        "applicationUrl": ingress.get("url") if ingress else None,
    }


def _required_secret_count(contract: dict[str, Any] | None) -> int:
    if not contract:
        return 0
    components = contract.get("components")
    if not isinstance(components, list):
        return 0
    names: set[str] = set()
    for component in components:
        if not isinstance(component, dict):
            continue
        secrets = component.get("secrets")
        if not isinstance(secrets, list):
            continue
        for secret in secrets:
            if isinstance(secret, dict) and secret.get("required", True):
                name = str(secret.get("name") or "").strip()
                if name:
                    names.add(name)
    return len(names)


def build_preflight(
    *,
    project_id: int,
    generation_id: int | None,
    environment_id: int | None,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    generation = (
        repository.find_generation_for_deployment(
            project_id=project_id,
            generation_id=generation_id,
        )
        if generation_id
        else None
    )

    artifacts_ready = bool(
        generation
        and generation.get("status") == "completed"
        and int(generation.get("artifact_count") or 0) > 0
        and int(generation.get("unapproved_artifact_count") or 0) == 0
    )
    checks.append(
        {
            "key": "artifacts",
            "label": "Artefacts approuvés",
            "description": (
                "La génération sélectionnée est complète et tous les fichiers sont approuvés."
                if artifacts_ready
                else "La génération doit être terminée et tous les artefacts doivent être approuvés."
            ),
            "status": "ready" if artifacts_ready else "blocked",
            "integrationName": None,
            "actionLabel": "Ouvrir la génération" if not artifacts_ready else None,
            "actionPath": (
                f"/projects/{project_id}/generation" if not artifacts_ready else None
            ),
        }
    )

    contract_row = repository.find_confirmed_contract(project_id)
    contract = contract_row.get("deployment_contract") if contract_row else None
    checks.append(
        {
            "key": "proposal",
            "label": "Proposition confirmée",
            "description": (
                "La proposition de déploiement et son contrat interne sont confirmés."
                if contract_row
                else "Confirmez la proposition de déploiement avant de continuer."
            ),
            "status": "ready" if contract_row else "blocked",
            "integrationName": None,
            "actionLabel": "Ouvrir la proposition" if not contract_row else None,
            "actionPath": (
                f"/projects/{project_id}/proposal" if not contract_row else None
            ),
        }
    )

    services = (
        repository.list_environment_connections(environment_id)
        if environment_id
        else []
    )
    by_role = {item["service_role"]: item for item in services}

    for role, label in REQUIRED_SERVICE_ROLES.items():
        service = by_role.get(role)
        if service is None:
            status = "blocked"
            description = f"Le service {label} n’est pas associé à l’environnement."
        elif not service.get("enabled"):
            status = "blocked"
            description = f"La connexion {service['name']} est désactivée."
        elif service.get("status") == "offline":
            status = "blocked"
            description = service.get("last_error") or "La connexion est hors ligne."
        elif service.get("status") in {"unchecked", "degraded", "not_configured"}:
            status = "warning"
            description = (
                service.get("last_error")
                or "La connexion sera testée de nouveau par le worker avant utilisation."
            )
        else:
            status = "ready"
            description = f"La connexion {service['name']} est disponible."

        checks.append(
            {
                "key": role,
                "label": label,
                "description": description,
                "status": status,
                "integrationName": service.get("name") if service else None,
                "actionLabel": "Ouvrir l’intégration",
                "actionPath": "/integrations",
            }
        )

    secret_count = _required_secret_count(contract)
    checks.append(
        {
            "key": "secrets",
            "label": "Secrets Kubernetes",
            "description": (
                f"{secret_count} secret(s) requis seront vérifiés dans le namespace avant la synchronisation."
                if secret_count
                else "Aucun secret applicatif obligatoire n’a été détecté."
            ),
            "status": "warning" if secret_count else "ready",
            "integrationName": None,
            "actionLabel": None,
            "actionPath": None,
        }
    )

    return checks


def _details_json(row: dict[str, Any]) -> dict[str, Any]:
    deployment_id = int(row["id"])
    steps = repository.list_deployment_steps(deployment_id)
    logs = repository.list_deployment_logs(deployment_id)
    components = repository.list_deployment_components(deployment_id)
    resources = repository.list_deployment_resources(deployment_id)
    incident = repository.find_current_incident(deployment_id)
    diagnostic = repository.find_diagnostic(deployment_id)
    corrections = repository.list_corrections(deployment_id)
    chat = repository.list_chat_messages(deployment_id)

    result = _summary_json(row)
    result.update(
        {
            "syncMode": row.get("sync_mode") or "confirm_before_sync",
            "preflight": build_preflight(
                project_id=int(row["project_id"]),
                generation_id=(
                    int(row["generation_run_id"])
                    if row.get("generation_run_id")
                    else None
                ),
                environment_id=(
                    int(row["environment_id"])
                    if row.get("environment_id")
                    else None
                ),
            ),
            "components": [_component_json(item) for item in components],
            "steps": [_step_json(item) for item in steps],
            "logs": [_log_json(item) for item in logs],
            "resources": [_resource_json(item) for item in resources],
            "incident": _incident_json(incident),
            "diagnostic": _diagnostic_json(diagnostic, corrections),
            "chat": [_chat_json(item) for item in chat],
            "health": _health_json(resources),
        }
    )
    return result


def list_deployments(filters: dict[str, Any]) -> dict[str, Any]:
    status = filters.get("status")
    if status and status not in ALLOWED_DEPLOYMENT_STATUSES:
        raise DeploymentServiceError(
            "INVALID_STATUS",
            "Le statut de déploiement est invalide.",
        )
    rows = repository.list_deployments(
        search=str(filters.get("search") or "").strip() or None,
        project_id=filters.get("project_id"),
        environment_id=filters.get("environment_id"),
        status=status,
        date_from=filters.get("date_from"),
        date_to=filters.get("date_to"),
    )
    return {
        "deployments": [_summary_json(row) for row in rows],
        "total": len(rows),
    }


def get_deployment(deployment_id: int) -> dict[str, Any]:
    row = repository.find_deployment(deployment_id)
    if row is None:
        raise DeploymentServiceError(
            "DEPLOYMENT_NOT_FOUND",
            "Le déploiement est introuvable.",
            404,
        )
    return _details_json(row)


def get_options() -> list[dict[str, Any]]:
    grouped: "OrderedDict[int, dict[str, Any]]" = OrderedDict()
    for row in repository.list_generation_options():
        project_id = int(row["project_id"])
        project = grouped.setdefault(
            project_id,
            {
                "id": project_id,
                "name": row["project_name"],
                "slug": row["project_slug"],
                "environmentId": int(row["environment_id"] or 0),
                "environmentName": row.get("environment_name") or "—",
                "namespace": row.get("namespace") or "default",
                "generations": [],
            },
        )
        generation_id = int(row["generation_id"])
        project["generations"].append(
            {
                "id": generation_id,
                "label": f"Génération #{generation_id}",
                "sourceCommit": row.get("source_commit") or "",
                "createdAt": _iso(row.get("generation_created_at")) or "",
                "componentCount": int(row.get("component_count") or 0),
                "approvedArtifactCount": int(
                    row.get("approved_artifact_count") or 0
                ),
            }
        )
    return list(grouped.values())


def get_project_readiness(project_id: int) -> dict[str, Any]:
    project = repository.find_project_for_deployment(project_id)
    if project is None:
        raise DeploymentServiceError(
            "PROJECT_NOT_FOUND",
            "Le projet est introuvable.",
            404,
        )
    generation = repository.find_latest_ready_generation(project_id)
    generation_id = int(generation["generation_id"]) if generation else None
    environment_id = (
        int(project["default_environment_id"])
        if project.get("default_environment_id")
        else None
    )
    checks = build_preflight(
        project_id=project_id,
        generation_id=generation_id,
        environment_id=environment_id,
    )
    return {
        "projectId": project_id,
        "projectName": project["name"],
        "generationId": generation_id,
        "generationLabel": (
            f"Génération #{generation_id}" if generation_id else None
        ),
        "sourceCommit": generation.get("source_commit") if generation else None,
        "environmentId": environment_id,
        "environmentName": project.get("environment_name"),
        "namespace": project.get("namespace"),
        "componentCount": int(generation.get("component_count") or 0)
        if generation
        else 0,
        "ready": not any(item["status"] == "blocked" for item in checks),
        "checks": checks,
    }


def _contract_component_by_id(
    contract: dict[str, Any] | None,
) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    if not contract:
        return result
    components = contract.get("components")
    if not isinstance(components, list):
        return result
    for item in components:
        if not isinstance(item, dict):
            continue
        try:
            result[int(item["id"])] = item
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _build_release_components(
    *,
    generation_id: int,
    project_slug: str,
    registry_host: str,
    contract: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    source_components = repository.list_generation_components(generation_id)
    artifacts = repository.list_generation_artifacts(generation_id)
    dockerfiles: dict[int, str] = {
        int(item["component_id"]): item["relative_path"]
        for item in artifacts
        if item.get("component_id")
        and item.get("artifact_type") == "dockerfile"
    }
    contract_components = _contract_component_by_id(contract)

    result: list[dict[str, Any]] = []
    for source in source_components:
        component_id = int(source["id"])
        configured = contract_components.get(component_id, {})
        component_slug = configured.get("slug") or _slug(source["name"])
        container = configured.get("container") if isinstance(configured, dict) else {}
        port = (
            container.get("port")
            if isinstance(container, dict)
            else source.get("detected_port")
        )
        result.append(
            {
                "component_id": component_id,
                "component_key": component_slug,
                "name": source["name"],
                "component_type": source.get("component_type") or "application",
                "root_path": source.get("root_path") or ".",
                "dockerfile_path": dockerfiles.get(component_id)
                or source.get("dockerfile_path")
                or str(
                    (configured.get("build") or {}).get("dockerfilePath")
                    if isinstance(configured, dict)
                    else "Dockerfile"
                ),
                "image_repository": (
                    f"{registry_host}/{project_slug}/{component_slug}".strip("/")
                ),
                "port": int(port) if port else None,
                "replicas": int(configured.get("replicas") or 1)
                if isinstance(configured, dict)
                else 1,
            }
        )
    return result


def create_deployment(payload: dict[str, Any], user_id: int) -> dict[str, Any]:
    project_id = _safe_int(payload.get("projectId"), "projectId")
    generation_id = _safe_int(payload.get("generationId"), "generationId")

    version = str(payload.get("version") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}", version):
        raise DeploymentServiceError(
            "INVALID_VERSION",
            "Le tag de version doit contenir uniquement lettres, chiffres, point, tiret ou underscore.",
        )

    sync_mode = str(payload.get("syncMode") or "confirm_before_sync")
    if sync_mode not in ALLOWED_SYNC_MODES:
        raise DeploymentServiceError(
            "INVALID_SYNC_MODE",
            "Le mode de synchronisation est invalide.",
        )

    note_value = payload.get("note")
    note = str(note_value).strip()[:4000] if note_value else None

    generation = repository.find_generation_for_deployment(
        project_id=project_id,
        generation_id=generation_id,
    )
    if generation is None:
        raise DeploymentServiceError(
            "GENERATION_NOT_FOUND",
            "La génération sélectionnée est introuvable pour ce projet.",
            404,
        )
    if generation.get("project_status") != "active":
        raise DeploymentServiceError(
            "PROJECT_NOT_ACTIVE",
            "Le projet doit être actif avant le déploiement.",
            409,
        )
    if generation.get("status") != "completed":
        raise DeploymentServiceError(
            "GENERATION_NOT_COMPLETED",
            "La génération sélectionnée n’est pas terminée.",
            409,
        )
    if int(generation.get("artifact_count") or 0) == 0:
        raise DeploymentServiceError(
            "NO_ARTIFACTS",
            "La génération ne contient aucun artefact.",
            409,
        )
    if int(generation.get("unapproved_artifact_count") or 0) > 0:
        raise DeploymentServiceError(
            "ARTIFACTS_NOT_APPROVED",
            "Tous les artefacts doivent être approuvés avant le déploiement.",
            409,
        )

    environment_id = generation.get("default_environment_id")
    if not environment_id:
        raise DeploymentServiceError(
            "ENVIRONMENT_REQUIRED",
            "Aucun environnement n’est associé au projet.",
            409,
        )

    contract_row = repository.find_confirmed_contract(project_id)
    if contract_row is None:
        raise DeploymentServiceError(
            "PROPOSAL_NOT_CONFIRMED",
            "La proposition de déploiement doit être confirmée.",
            409,
        )
    contract = contract_row.get("deployment_contract") or {}

    registry = repository.find_environment_connection(
        environment_id=int(environment_id),
        service_role="container_registry",
    )
    if registry is None:
        raise DeploymentServiceError(
            "REGISTRY_REQUIRED",
            "L’environnement ne contient aucun registre de conteneurs.",
            409,
        )
    registry_host = (
        ((contract.get("target") or {}).get("registry") or {}).get("host")
        or _registry_host(str(registry.get("base_url") or ""))
    )
    if not registry_host:
        raise DeploymentServiceError(
            "REGISTRY_HOST_MISSING",
            "L’adresse du registre Nexus est absente.",
            409,
        )

    components = _build_release_components(
        generation_id=generation_id,
        project_slug=generation["project_slug"],
        registry_host=registry_host,
        contract=contract,
    )
    if not components:
        raise DeploymentServiceError(
            "NO_DEPLOYABLE_COMPONENT",
            "Aucun composant déployable n’est disponible.",
            409,
        )

    row = repository.create_deployment(
        project_id=project_id,
        generation_id=generation_id,
        environment_id=int(environment_id),
        environment_name=generation.get("environment_name") or "environment",
        source_commit=generation.get("analyzed_commit_sha") or "",
        version=version,
        note=note,
        sync_mode=sync_mode,
        user_id=user_id,
        components=components,
    )
    repository.add_log(
        deployment_id=int(row["id"]),
        scope="system",
        level="info",
        message=(
            "Déploiement créé. Les prérequis seront vérifiés avant le lancement."
        ),
    )
    return get_deployment(int(row["id"]))


def start_deployment(deployment_id: int) -> dict[str, Any]:
    row = repository.find_deployment(deployment_id)
    if row is None:
        raise DeploymentServiceError(
            "DEPLOYMENT_NOT_FOUND",
            "Le déploiement est introuvable.",
            404,
        )
    if row.get("status") != "ready":
        raise DeploymentServiceError(
            "DEPLOYMENT_NOT_READY",
            "Seul un déploiement prêt peut être lancé.",
            409,
        )
    checks = build_preflight(
        project_id=int(row["project_id"]),
        generation_id=int(row["generation_run_id"]),
        environment_id=int(row["environment_id"]),
    )
    blockers = [item for item in checks if item["status"] == "blocked"]
    if blockers:
        raise DeploymentServiceError(
            "PREFLIGHT_BLOCKED",
            "Le déploiement est bloqué : "
            + ", ".join(item["label"] for item in blockers),
            409,
        )
    queued = repository.queue_deployment(deployment_id)
    if queued is None:
        raise DeploymentServiceError(
            "DEPLOYMENT_QUEUE_FAILED",
            "Le déploiement ne peut pas être ajouté à la file.",
            409,
        )
    repository.add_log(
        deployment_id=deployment_id,
        scope="system",
        level="info",
        message="Le déploiement a été ajouté à la file du worker.",
    )
    return get_deployment(deployment_id)


def cancel_deployment(deployment_id: int) -> dict[str, Any]:
    row = repository.find_deployment(deployment_id)
    if row is None:
        raise DeploymentServiceError(
            "DEPLOYMENT_NOT_FOUND",
            "Le déploiement est introuvable.",
            404,
        )
    status = row.get("status")
    if status not in {"queued", "running", "waiting_confirmation"}:
        raise DeploymentServiceError(
            "DEPLOYMENT_NOT_CANCELLABLE",
            "Ce déploiement ne peut plus être annulé.",
            409,
        )
    if status in {"queued", "waiting_confirmation"}:
        repository.update_deployment(
            deployment_id,
            status="cancelled",
            current_stage_label="Annulé par l’utilisateur",
            cancel_requested=True,
            finished_at=datetime.now(timezone.utc),
            locked_at=None,
            locked_by=None,
        )
        current_stage = row.get("current_stage")
        if current_stage:
            repository.update_step(
                deployment_id=deployment_id,
                stage=current_stage,
                status="cancelled",
            )
    else:
        repository.request_cancellation(deployment_id)
    repository.add_log(
        deployment_id=deployment_id,
        scope="system",
        level="warning",
        message="L’utilisateur a demandé l’annulation du déploiement.",
    )
    return get_deployment(deployment_id)


def retry_deployment(deployment_id: int) -> dict[str, Any]:
    row = repository.find_deployment(deployment_id)
    if row is None:
        raise DeploymentServiceError(
            "DEPLOYMENT_NOT_FOUND",
            "Le déploiement est introuvable.",
            404,
        )
    if row.get("status") != "failed":
        raise DeploymentServiceError(
            "DEPLOYMENT_NOT_FAILED",
            "Seul un déploiement échoué peut être relancé.",
            409,
        )
    incident = repository.find_current_incident(deployment_id)
    if incident is None:
        raise DeploymentServiceError(
            "INCIDENT_NOT_FOUND",
            "Aucun incident actif n’est associé au déploiement.",
            409,
        )
    if not incident.get("retryable"):
        raise DeploymentServiceError(
            "INCIDENT_NOT_RETRYABLE",
            "Cette erreur exige une correction avant un nouveau déploiement.",
            409,
        )
    if incident.get("requires_new_generation"):
        raise DeploymentServiceError(
            "NEW_GENERATION_REQUIRED",
            "Créez une nouvelle révision de génération avant de déployer de nouveau.",
            409,
        )

    stage = incident.get("stage") or "prepare"
    repository.reset_steps_from_stage(deployment_id, stage)
    repository.resolve_incidents(deployment_id)
    repository.save_diagnostic(
        deployment_id=deployment_id,
        status="idle",
        evidence=[],
    )
    repository.update_deployment(
        deployment_id,
        status="ready",
        current_stage=stage,
        current_stage_label=f"Nouvelle tentative : {STAGE_LABELS.get(stage, stage)}",
        error_code=None,
        error_message=None,
        cancel_requested=False,
        finished_at=None,
        locked_at=None,
        locked_by=None,
    )
    repository.queue_deployment(deployment_id)
    repository.add_log(
        deployment_id=deployment_id,
        scope="system",
        level="info",
        stage=stage,
        message="Nouvelle tentative lancée depuis l’étape échouée.",
    )
    return get_deployment(deployment_id)


def confirm_synchronization(deployment_id: int) -> dict[str, Any]:
    row = repository.find_deployment(deployment_id)
    if row is None:
        raise DeploymentServiceError(
            "DEPLOYMENT_NOT_FOUND",
            "Le déploiement est introuvable.",
            404,
        )
    if row.get("status") != "waiting_confirmation":
        raise DeploymentServiceError(
            "SYNCHRONIZATION_NOT_WAITING",
            "Ce déploiement n’attend pas de confirmation Argo CD.",
            409,
        )
    repository.update_deployment(
        deployment_id,
        status="ready",
        current_stage="argocd",
        current_stage_label="Synchronisation Argo CD confirmée",
        sync_confirmed_at=datetime.now(timezone.utc),
        cancel_requested=False,
        locked_at=None,
        locked_by=None,
    )
    repository.queue_deployment(deployment_id)
    repository.add_log(
        deployment_id=deployment_id,
        scope="argocd",
        level="info",
        stage="argocd",
        message="La synchronisation Argo CD a été confirmée par l’utilisateur.",
    )
    return get_deployment(deployment_id)


def request_diagnosis(deployment_id: int) -> dict[str, Any]:
    row = repository.find_deployment(deployment_id)
    if row is None:
        raise DeploymentServiceError(
            "DEPLOYMENT_NOT_FOUND",
            "Le déploiement est introuvable.",
            404,
        )
    incident = repository.find_current_incident(deployment_id)
    if incident is None:
        raise DeploymentServiceError(
            "INCIDENT_REQUIRED",
            "Aucune erreur active ne nécessite de diagnostic.",
            409,
        )

    repository.save_diagnostic(
        deployment_id=deployment_id,
        status="running",
        evidence=[],
    )
    logs = repository.list_deployment_logs(deployment_id)
    resources = repository.list_deployment_resources(deployment_id)
    ai_connection = repository.find_environment_connection(
        environment_id=int(row["environment_id"]),
        service_role="ai_provider",
    )
    result = diagnose_with_ai(
        ai_connection=ai_connection,
        deployment=row,
        incident=incident,
        logs=logs,
        resources=resources,
    )
    repository.save_diagnostic(
        deployment_id=deployment_id,
        status="completed",
        cause=result["cause"],
        explanation=result["explanation"],
        confidence=result["confidence"],
        target_phase=result["target_phase"],
        evidence=result["evidence"],
        provider_connection_id=result.get("provider_connection_id"),
        model=result.get("model"),
        raw_response=result.get("raw"),
    )
    repository.replace_corrections(
        deployment_id,
        result.get("corrections") or [],
    )
    repository.add_chat_message(
        deployment_id=deployment_id,
        role="assistant",
        content=f"{result['cause']}\n\n{result['explanation']}",
    )
    details = get_deployment(deployment_id)
    return details["diagnostic"]


def send_diagnostic_message(
    *,
    deployment_id: int,
    content: str,
    user_id: int,
) -> list[dict[str, Any]]:
    normalized = content.strip()
    if not normalized:
        raise DeploymentServiceError(
            "MESSAGE_REQUIRED",
            "Le message est obligatoire.",
        )
    if len(normalized) > 6000:
        raise DeploymentServiceError(
            "MESSAGE_TOO_LONG",
            "Le message ne doit pas dépasser 6000 caractères.",
        )

    row = repository.find_deployment(deployment_id)
    if row is None:
        raise DeploymentServiceError(
            "DEPLOYMENT_NOT_FOUND",
            "Le déploiement est introuvable.",
            404,
        )

    repository.add_chat_message(
        deployment_id=deployment_id,
        role="user",
        content=normalized,
        user_id=user_id,
    )
    incident = repository.find_current_incident(deployment_id)
    diagnostic = repository.find_diagnostic(deployment_id)
    existing_messages = repository.list_chat_messages(deployment_id)
    ai_connection = repository.find_environment_connection(
        environment_id=int(row["environment_id"]),
        service_role="ai_provider",
    )
    answer = chat_with_ai(
        ai_connection=ai_connection,
        deployment=row,
        incident=incident,
        diagnostic=diagnostic,
        messages=existing_messages,
    )
    repository.add_chat_message(
        deployment_id=deployment_id,
        role="assistant",
        content=answer,
    )
    return [
        _chat_json(item)
        for item in repository.list_chat_messages(deployment_id)
    ]


def approve_correction(
    *,
    deployment_id: int,
    correction_id: int,
    user_id: int,
) -> dict[str, Any]:
    row = repository.approve_correction(
        deployment_id=deployment_id,
        correction_id=correction_id,
        user_id=user_id,
    )
    if row is None:
        raise DeploymentServiceError(
            "CORRECTION_NOT_FOUND",
            "La correction est introuvable ou déjà traitée.",
            404,
        )

    target_phase = row.get("target_phase") or "deployment"
    if target_phase == "integration":
        system_message = (
            "Correction approuvée. Ouvrez l’intégration concernée, corrigez le "
            "credential, testez-la puis revenez relancer depuis l’étape échouée."
        )
    elif target_phase in {"analysis", "proposal", "generation"}:
        system_message = (
            "Correction approuvée. Le backend de la phase concernée devra créer "
            "une nouvelle révision avant tout nouveau déploiement. La version "
            "échouée reste conservée dans l’historique."
        )
    else:
        system_message = (
            "Correction approuvée. Elle sera prise en compte lors de la prochaine "
            "tentative contrôlée."
        )
    repository.add_chat_message(
        deployment_id=deployment_id,
        role="system",
        content=system_message,
        user_id=user_id,
    )
    details = get_deployment(deployment_id)
    return details["diagnostic"]
