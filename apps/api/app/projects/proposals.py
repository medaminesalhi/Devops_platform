from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests
from flask import current_app, g, jsonify, request

from app.auth.decorators import require_auth, require_project_access
from app.database import get_database_connection
from app.integrations.discovery import RepositoryDiscoveryError, discover_repositories
from app.integrations.security import decrypt_credential
from app.projects.routes import error_response, projects_blueprint


PROPOSAL_MODES = {"hybrid", "deterministic"}
PROPOSAL_STATUSES = {"preparing", "needs_input", "ready", "confirmed", "failed"}
EXPOSURE_MODES = {"internal", "public"}
PERSISTENCE_CHOICES = {"none", "suggested", "required"}
MIGRATION_CHOICES = {"automatic", "enabled", "disabled"}
DELIVERY_MODES = {"git", "helm"}
GIT_REFRESH_MODES = {"polling", "webhook"}
SERVICE_TYPES = {"ClusterIP", "NodePort", "LoadBalancer"}

# GitLab GitOps n'est plus obligatoire : un environnement peut utiliser
# un repository Helm Nexus comme source Argo CD.
REQUIRED_ENVIRONMENT_ROLES = {
    "kubernetes",
    "argocd",
    "container_registry",
}

SENSITIVE_ENV_MARKERS = (
    "PASSWORD",
    "PASSWD",
    "SECRET",
    "TOKEN",
    "PRIVATE_KEY",
    "API_KEY",
    "CREDENTIAL",
)


class ProposalError(RuntimeError):
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


@dataclass(frozen=True)
class ProposalContext:
    project: dict[str, Any]
    analysis: dict[str, Any]
    environment: dict[str, Any]
    components: list[dict[str, Any]]
    services: list[dict[str, Any]]


def _user_id() -> int:
    return int(g.current_user["id"])


def _json_object() -> dict[str, Any]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ProposalError("INVALID_JSON", "Le corps JSON est invalide.")
    return payload


def _json_value(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return fallback
        return parsed
    return fallback


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized or "application"


def _safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _safe_text(value: Any, default: str = "", maximum: int = 1000) -> str:
    if value is None:
        return default
    return str(value).strip()[:maximum]


def _normalize_domain(value: Any) -> str | None:
    domain = _safe_text(value, maximum=255).lower().rstrip(".")
    if not domain:
        return None
    if not re.fullmatch(
        r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
        r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
        domain,
    ):
        raise ProposalError("INVALID_DOMAIN", "Le nom de domaine est invalide.")
    return domain


def _normalize_namespace(value: Any, fallback: str) -> str:
    namespace = _safe_text(value, fallback, 63).lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", namespace):
        raise ProposalError(
            "INVALID_NAMESPACE",
            "Le namespace Kubernetes est invalide.",
        )
    return namespace


def _service_credential(service: dict[str, Any] | None) -> str | None:
    if service is None:
        return None
    ciphertext = service.get("secret_ciphertext")
    return decrypt_credential(ciphertext) if ciphertext else None


def _repository_options(context: ProposalContext) -> dict[str, list[dict[str, Any]]]:
    nexus = next(
        (item for item in context.services if item.get("service_role") == "container_registry"),
        None,
    )
    gitlab = next(
        (item for item in context.services if item.get("service_role") == "gitops_repository"),
        None,
    )

    docker: list[dict[str, Any]] = []
    helm: list[dict[str, Any]] = []
    git: list[dict[str, Any]] = []

    if nexus is not None:
        try:
            discovered = discover_repositories(nexus, _service_credential(nexus))
        except RepositoryDiscoveryError as error:
            raise ProposalError(
                "NEXUS_REPOSITORY_DISCOVERY_FAILED",
                f"Impossible de découvrir les repositories Nexus : {error.message}",
                502,
            ) from error
        docker = [
            item for item in discovered
            if item.get("format") == "docker" and item.get("type") == "hosted"
        ]
        helm = [
            item for item in discovered
            if item.get("format") == "helm" and item.get("type") == "hosted"
        ]

    if gitlab is not None:
        try:
            git = discover_repositories(gitlab, _service_credential(gitlab))
        except RepositoryDiscoveryError as error:
            # Le mode Helm doit rester utilisable si GitLab est indisponible.
            current_app.logger.warning("GitLab repository discovery failed: %s", error.message)
            git = []

    return {"docker": docker, "helm": helm, "git": git}


def _find_repository(
    repositories: list[dict[str, Any]],
    *,
    name: str | None = None,
    repository_id: str | int | None = None,
) -> dict[str, Any] | None:
    for item in repositories:
        if name and str(item.get("name") or "") == name:
            return item
        if repository_id is not None and str(item.get("id") or "") == str(repository_id):
            return item
    return None


def _normalize_probe_path(value: Any, fallback: str) -> str:
    path = _safe_text(value, fallback, 255) or fallback
    if not path.startswith("/"):
        raise ProposalError("INVALID_PROBE_PATH", "Une sonde HTTP doit commencer par /.")
    return path


def _validate_decisions(
    value: Any,
    context: ProposalContext,
) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    environment = context.environment
    repositories = _repository_options(context)

    exposure_mode = _safe_text(raw.get("exposureMode"), "internal", 20)
    persistence = _safe_text(raw.get("persistence"), "suggested", 20)
    migration = _safe_text(raw.get("migration"), "automatic", 20)
    delivery_mode = _safe_text(
        raw.get("deliveryMode"),
        "git" if repositories["git"] else "helm",
        20,
    )
    git_refresh_mode = _safe_text(raw.get("gitRefreshMode"), "polling", 20)

    if exposure_mode not in EXPOSURE_MODES:
        raise ProposalError("INVALID_EXPOSURE_MODE", "Le mode d'exposition est invalide.")
    if persistence not in PERSISTENCE_CHOICES:
        raise ProposalError("INVALID_PERSISTENCE", "Le choix de stockage est invalide.")
    if migration not in MIGRATION_CHOICES:
        raise ProposalError("INVALID_MIGRATION", "Le choix de migration est invalide.")
    if delivery_mode not in DELIVERY_MODES:
        raise ProposalError("INVALID_DELIVERY_MODE", "La source Argo CD sélectionnée est invalide.")
    if git_refresh_mode not in GIT_REFRESH_MODES:
        raise ProposalError("INVALID_GIT_REFRESH_MODE", "Le mode de rafraîchissement Git est invalide.")

    docker_name = _safe_text(raw.get("imageRepositoryName"), maximum=200)
    docker_repository = _find_repository(repositories["docker"], name=docker_name)
    if docker_repository is None and not docker_name and repositories["docker"]:
        docker_repository = repositories["docker"][0]
        docker_name = str(docker_repository.get("name") or "")
    if docker_repository is None:
        raise ProposalError(
            "DOCKER_REPOSITORY_REQUIRED",
            "Sélectionnez un repository Docker hosted détecté dans Nexus.",
            409,
        )
    if not docker_repository.get("endpointUrl"):
        raise ProposalError(
            "DOCKER_REPOSITORY_ENDPOINT_MISSING",
            f"Le repository Docker {docker_name} ne possède pas d'endpoint Docker exploitable.",
            409,
        )
    docker_metadata = docker_repository.get("metadata") or {}
    if isinstance(docker_metadata, dict) and docker_metadata.get("endpointReachable") is False:
        raise ProposalError(
            "DOCKER_REPOSITORY_UNREACHABLE",
            f"Le repository Docker {docker_name} est détecté mais son endpoint n'est pas joignable : "
            f"{docker_metadata.get('endpointError') or docker_repository.get('endpointUrl')}",
            409,
        )

    git_repository_id = raw.get("gitRepositoryId")
    git_repository: dict[str, Any] | None = None
    helm_name = _safe_text(raw.get("helmRepositoryName"), maximum=200)
    helm_repository: dict[str, Any] | None = None

    if delivery_mode == "git":
        git_repository = _find_repository(
            repositories["git"],
            repository_id=git_repository_id,
        )
        if git_repository is None and git_repository_id in (None, "") and repositories["git"]:
            git_repository = repositories["git"][0]
            git_repository_id = git_repository.get("projectId") or git_repository.get("id")
        if git_repository is None:
            raise ProposalError(
                "GITOPS_REPOSITORY_REQUIRED",
                "Sélectionnez un repository GitLab pour le mode GitOps Git.",
                409,
            )
    else:
        helm_repository = _find_repository(repositories["helm"], name=helm_name)
        if helm_repository is None and not helm_name and repositories["helm"]:
            helm_repository = repositories["helm"][0]
            helm_name = str(helm_repository.get("name") or "")
        if helm_repository is None:
            raise ProposalError(
                "HELM_REPOSITORY_REQUIRED",
                "Sélectionnez un repository Helm hosted détecté dans Nexus.",
                409,
            )
        helm_metadata = helm_repository.get("metadata") or {}
        if isinstance(helm_metadata, dict) and helm_metadata.get("endpointReachable") is False:
            raise ProposalError(
                "HELM_REPOSITORY_UNREACHABLE",
                f"Le repository Helm {helm_name} est détecté mais son URL n'est pas joignable : "
                f"{helm_metadata.get('endpointError') or helm_repository.get('url')}",
                409,
            )

    domain = _normalize_domain(raw.get("domain"))
    if exposure_mode == "public" and not domain:
        domain = _normalize_domain(environment.get("domain"))

    advanced_raw = raw.get("advanced") if isinstance(raw.get("advanced"), dict) else {}
    service_type = _safe_text(advanced_raw.get("serviceType"), "ClusterIP", 30)
    if service_type not in SERVICE_TYPES:
        raise ProposalError("INVALID_SERVICE_TYPE", "Le type de Service Kubernetes est invalide.")

    port_value = advanced_raw.get("port")
    port = None if port_value in (None, "", 0, "0") else _safe_int(port_value, 8080, 1, 65535)

    return {
        "namespace": _normalize_namespace(
            raw.get("namespace"),
            environment.get("namespace") or "default",
        ),
        "exposureMode": exposure_mode,
        "domain": domain,
        "replicas": _safe_int(raw.get("replicas"), 1, 1, 20),
        "persistence": persistence,
        "migration": migration,
        "imageRepositoryName": docker_name,
        "deliveryMode": delivery_mode,
        "gitRepositoryId": (
            int(git_repository.get("projectId") or git_repository.get("id"))
            if git_repository is not None else None
        ),
        "gitBranch": _safe_text(
            raw.get("gitBranch"),
            str((git_repository or {}).get("defaultBranch") or "main"),
            200,
        ) or "main",
        "gitRefreshMode": git_refresh_mode,
        "helmRepositoryName": helm_name or None,
        "advanced": {
            "startCommand": _safe_text(advanced_raw.get("startCommand"), maximum=1000) or None,
            "port": port,
            "serviceType": service_type,
            "readinessPath": _normalize_probe_path(advanced_raw.get("readinessPath"), "/health"),
            "livenessPath": _normalize_probe_path(advanced_raw.get("livenessPath"), "/health"),
            "cpuRequest": _safe_text(advanced_raw.get("cpuRequest"), "100m", 50),
            "cpuLimit": _safe_text(advanced_raw.get("cpuLimit"), "500m", 50),
            "memoryRequest": _safe_text(advanced_raw.get("memoryRequest"), "128Mi", 50),
            "memoryLimit": _safe_text(advanced_raw.get("memoryLimit"), "512Mi", 50),
        },
    }


def _load_context(project_id: int) -> ProposalContext:
    with get_database_connection() as connection:
        project = connection.execute(
            """
                SELECT
                    project.id,
                    project.name,
                    project.slug,
                    project.description,
                    project.status,
                    project.default_environment_id,
                    project.analysis_status,
                    environment.id AS environment_id,
                    environment.name AS environment_name,
                    environment.code AS environment_code,
                    environment.environment_type,
                    environment.description AS environment_description,
                    environment.namespace,
                    environment.domain,
                    environment.configuration_status,
                    environment.is_default
                FROM projects AS project
                LEFT JOIN deployment_environments AS environment
                  ON environment.id = project.default_environment_id
                WHERE project.id = %s
                  AND project.archived_at IS NULL
                LIMIT 1;
            """,
            (project_id,),
        ).fetchone()

        if project is None:
            raise ProposalError("PROJECT_NOT_FOUND", "Le projet est introuvable.", 404)
        if project.get("status") != "active":
            raise ProposalError(
                "PROJECT_NOT_ACTIVE",
                "Le projet doit être activé avant de préparer le déploiement.",
                409,
            )
        if not project.get("environment_id"):
            raise ProposalError(
                "ENVIRONMENT_REQUIRED",
                "Aucun environnement n'est associé au projet.",
                409,
            )

        analysis = connection.execute(
            """
                SELECT
                    id,
                    project_id,
                    analyzed_commit_sha,
                    selected_subdirectory,
                    status,
                    summary,
                    confirmed_at
                FROM project_analysis_runs
                WHERE project_id = %s
                  AND status = 'confirmed'
                ORDER BY confirmed_at DESC NULLS LAST, created_at DESC
                LIMIT 1;
            """,
            (project_id,),
        ).fetchone()
        if analysis is None:
            raise ProposalError(
                "ANALYSIS_NOT_CONFIRMED",
                "Confirmez l'analyse avant de préparer la proposition de déploiement.",
                409,
            )

        components = connection.execute(
            """
                SELECT
                    id,
                    project_id,
                    analysis_run_id,
                    name,
                    component_type,
                    root_path,
                    runtime,
                    framework,
                    package_manager,
                    build_command,
                    start_command,
                    detected_port,
                    deployable,
                    dockerfile_path,
                    helm_chart_path,
                    kubernetes_paths,
                    environment_variables,
                    confidence,
                    configuration,
                    user_modified
                FROM project_components
                WHERE analysis_run_id = %s
                ORDER BY root_path, name;
            """,
            (analysis["id"],),
        ).fetchall()

        services = connection.execute(
            """
                SELECT
                    link.service_role,
                    link.is_required,
                    integration.id AS connection_id,
                    integration.name AS connection_name,
                    integration.provider_type,
                    integration.base_url,
                    integration.description,
                    integration.enabled,
                    integration.verify_ssl,
                    integration.status,
                    integration.last_checked_at,
                    integration.last_latency_ms,
                    COALESCE(credential.auth_type, 'none') AS auth_type,
                    credential.username,
                    credential.secret_ciphertext,
                    (credential.secret_ciphertext IS NOT NULL) AS credential_configured
                FROM environment_connections AS link
                INNER JOIN integration_connections AS integration
                  ON integration.id = link.connection_id
                LEFT JOIN integration_credentials AS credential
                  ON credential.connection_id = integration.id
                WHERE link.environment_id = %s
                ORDER BY link.service_role;
            """,
            (project["environment_id"],),
        ).fetchall()

    environment = {
        "id": int(project["environment_id"]),
        "name": project["environment_name"],
        "code": project.get("environment_code") or _slug(project["environment_name"]),
        "environment_type": project.get("environment_type") or "custom",
        "description": project.get("environment_description"),
        "namespace": project.get("namespace") or "default",
        "domain": project.get("domain"),
        "configuration_status": project.get("configuration_status") or "active",
        "is_default": bool(project.get("is_default")),
    }

    configured_roles = {item["service_role"] for item in services}
    missing = sorted(REQUIRED_ENVIRONMENT_ROLES - configured_roles)
    if missing:
        raise ProposalError(
            "ENVIRONMENT_INCOMPLETE",
            "L'environnement ne contient pas tous les services requis : " + ", ".join(missing),
            409,
        )

    return ProposalContext(
        project=dict(project),
        analysis=dict(analysis),
        environment=environment,
        components=[dict(item) for item in components],
        services=[dict(item) for item in services],
    )


def _environment_json(context: ProposalContext) -> dict[str, Any]:
    return {
        "id": context.environment["id"],
        "name": context.environment["name"],
        "code": context.environment["code"],
        "environmentType": context.environment["environment_type"],
        "description": context.environment.get("description"),
        "namespace": context.environment["namespace"],
        "domain": context.environment.get("domain"),
        "configurationStatus": context.environment["configuration_status"],
        "isDefault": context.environment["is_default"],
        "services": [
            {
                "role": item["service_role"],
                "required": bool(item["is_required"]),
                "connectionId": item["connection_id"],
                "connectionName": item["connection_name"],
                "providerType": item["provider_type"],
                "baseUrl": item["base_url"],
                "status": item["status"],
                "lastCheckedAt": _iso(item.get("last_checked_at")),
                "lastLatencyMs": item.get("last_latency_ms"),
            }
            for item in context.services
        ],
    }


def _runtime_parts(runtime: str | None) -> tuple[str, str]:
    value = _safe_text(runtime, "unknown", 100)
    match = re.match(r"(?P<name>[A-Za-z.+#_-]+)\s*(?P<version>.*)", value)
    if not match:
        return value, ""
    return match.group("name"), match.group("version").strip()


def _default_port(component: dict[str, Any]) -> int:
    detected = component.get("detected_port")
    if detected:
        return _safe_int(detected, 8080, 1, 65535)
    framework = _safe_text(component.get("framework")).lower()
    component_type = _safe_text(component.get("component_type")).lower()
    if "flask" in framework or "django" in framework or "fastapi" in framework:
        return 8000
    if "spring" in framework:
        return 8080
    if "angular" in framework or "react" in framework or "vue" in framework:
        return 8080
    if component_type in {"worker", "job", "library"}:
        return 8080
    return 8080


def _install_command(component: dict[str, Any]) -> str:
    package_manager = _safe_text(component.get("package_manager")).lower()
    runtime = _safe_text(component.get("runtime")).lower()
    if package_manager in {"npm", "pnpm", "yarn"}:
        return {"npm": "npm ci", "pnpm": "pnpm install --frozen-lockfile", "yarn": "yarn install --frozen-lockfile"}[package_manager]
    if package_manager in {"pip", "pip3", "poetry", "uv"} or "python" in runtime:
        if package_manager == "poetry":
            return "poetry install --only main --no-interaction --no-ansi"
        if package_manager == "uv":
            return "uv sync --frozen --no-dev"
        return "pip install --no-cache-dir -r requirements.txt"
    if package_manager in {"maven", "mvn"}:
        return "mvn dependency:go-offline"
    if package_manager == "gradle":
        return "./gradlew dependencies"
    if "go" in runtime:
        return "go mod download"
    return ""


def _build_command(component: dict[str, Any]) -> str:
    existing = _safe_text(component.get("build_command"), maximum=1000)
    if existing:
        return existing
    package_manager = _safe_text(component.get("package_manager")).lower()
    runtime = _safe_text(component.get("runtime")).lower()
    framework = _safe_text(component.get("framework")).lower()
    if package_manager in {"npm", "pnpm", "yarn"}:
        return f"{package_manager} run build"
    if package_manager in {"maven", "mvn"}:
        return "mvn -DskipTests package"
    if package_manager == "gradle":
        return "./gradlew build -x test"
    if "go" in runtime:
        return "go build -o /out/app ./..."
    if "angular" in framework:
        return "npm run build"
    return ""


def _start_command(component: dict[str, Any], port: int) -> str:
    existing = _safe_text(component.get("start_command"), maximum=1000)
    if existing:
        return existing
    framework = _safe_text(component.get("framework")).lower()
    runtime = _safe_text(component.get("runtime")).lower()
    if "flask" in framework:
        return f"gunicorn --bind 0.0.0.0:{port} wsgi:app"
    if "django" in framework:
        return f"gunicorn --bind 0.0.0.0:{port} config.wsgi:application"
    if "fastapi" in framework:
        return f"uvicorn main:app --host 0.0.0.0 --port {port}"
    if "spring" in framework or "java" in runtime:
        return "java -jar app.jar"
    if "node" in runtime:
        return "node dist/server.js"
    if "angular" in framework or "react" in framework or "vue" in framework:
        return "nginx -g 'daemon off;'"
    if "go" in runtime:
        return "/app/app"
    return ""


def _migration_command(component: dict[str, Any]) -> str | None:
    framework = _safe_text(component.get("framework")).lower()
    configuration = _json_value(component.get("configuration"), {})
    explicit = _safe_text(configuration.get("migrationCommand") if isinstance(configuration, dict) else None)
    if explicit:
        return explicit
    if "django" in framework:
        return "python manage.py migrate"
    if "flask" in framework or "alembic" in framework:
        return "flask db upgrade"
    if "rails" in framework:
        return "bundle exec rails db:migrate"
    return None


def _resource_profile(environment_type: str, component_type: str) -> dict[str, str]:
    environment_type = environment_type.lower()
    component_type = component_type.lower()
    if environment_type == "production":
        return {
            "cpuRequest": "200m",
            "cpuLimit": "1000m",
            "memoryRequest": "256Mi",
            "memoryLimit": "1Gi",
        }
    if component_type in {"worker", "job"}:
        return {
            "cpuRequest": "100m",
            "cpuLimit": "500m",
            "memoryRequest": "128Mi",
            "memoryLimit": "512Mi",
        }
    return {
        "cpuRequest": "100m",
        "cpuLimit": "500m",
        "memoryRequest": "128Mi",
        "memoryLimit": "512Mi",
    }


def _host_for_component(
    *,
    component: dict[str, Any],
    decisions: dict[str, Any],
    component_count: int,
) -> str | None:
    if decisions["exposureMode"] != "public" or not decisions.get("domain"):
        return None
    domain = decisions["domain"]
    component_type = _safe_text(component.get("component_type")).lower()
    framework = _safe_text(component.get("framework")).lower()
    if component_count == 1 or component_type in {"frontend", "web"} or any(
        token in framework for token in ("angular", "react", "vue")
    ):
        return domain
    return f"{_slug(component['name'])}.{domain}"


def _build_default_components(
    context: ProposalContext,
    decisions: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    deployable = [item for item in context.components if bool(item.get("deployable", True))]
    proposals: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    warnings: list[str] = []

    advanced = decisions.get("advanced") or {}

    for component in deployable:
        port = int(advanced.get("port") or _default_port(component))
        migration_command = _migration_command(component)
        migration_enabled = decisions["migration"] == "enabled" or (
            decisions["migration"] == "automatic" and migration_command is not None
        )
        persistence_enabled = decisions["persistence"] == "required"
        resources = _resource_profile(
            context.environment["environment_type"],
            _safe_text(component.get("component_type")),
        )
        for key in ("cpuRequest", "cpuLimit", "memoryRequest", "memoryLimit"):
            if advanced.get(key):
                resources[key] = str(advanced[key])
        host = _host_for_component(
            component=component,
            decisions=decisions,
            component_count=len(deployable),
        )
        component_warnings: list[str] = []

        if not component.get("detected_port"):
            component_warnings.append(f"Port {port} proposé par défaut.")
        if not component.get("start_command"):
            component_warnings.append("Commande de démarrage proposée automatiquement.")
        if migration_command and decisions["migration"] == "automatic":
            question_id = f"migration-{component['id']}"
            questions.append(
                {
                    "id": question_id,
                    "componentId": int(component["id"]),
                    "label": f"Confirmer la migration de {component['name']}",
                    "description": f"SApixi propose d'exécuter : {migration_command}",
                    "required": True,
                    "answer": None,
                    "choices": ["enabled", "disabled"],
                }
            )

        proposals.append(
            {
                "componentId": int(component["id"]),
                "name": component["name"],
                "componentType": component.get("component_type") or "unknown",
                "runtime": component.get("runtime") or "unknown",
                "framework": component.get("framework") or "",
                "confidence": _safe_int(component.get("confidence"), 50, 0, 100),
                "summary": "Configuration préparée à partir de l'analyse confirmée.",
                "docker": {
                    "strategy": "existing" if component.get("dockerfile_path") else "generated-multistage",
                    "installCommand": _install_command(component),
                    "buildCommand": _build_command(component),
                    "startCommand": advanced.get("startCommand") or _start_command(component, port),
                    "port": port,
                },
                "kubernetes": {
                    "serviceType": advanced.get("serviceType") or "ClusterIP",
                    "ingressEnabled": host is not None,
                    "host": host,
                    "replicas": decisions["replicas"],
                    "readinessPath": advanced.get("readinessPath") or ("/health" if "api" in _safe_text(component.get("component_type")).lower() else "/"),
                    "livenessPath": advanced.get("livenessPath") or ("/health" if "api" in _safe_text(component.get("component_type")).lower() else "/"),
                    **resources,
                },
                "persistence": {
                    "enabled": persistence_enabled,
                    "mountPath": "/data" if persistence_enabled else None,
                    "size": "5Gi" if persistence_enabled else None,
                },
                "migration": {
                    "enabled": migration_enabled,
                    "command": migration_command,
                    "requiresConfirmation": migration_command is not None and decisions["migration"] == "automatic",
                },
                "warnings": component_warnings,
            }
        )
        warnings.extend(f"{component['name']} : {item}" for item in component_warnings)

    if decisions["exposureMode"] == "public" and not decisions.get("domain"):
        questions.append(
            {
                "id": "public-domain",
                "componentId": None,
                "label": "Nom de domaine",
                "description": "Indiquez le domaine à utiliser pour l'Ingress.",
                "required": True,
                "answer": None,
                "choices": [],
            }
        )

    if not proposals:
        raise ProposalError(
            "NO_DEPLOYABLE_COMPONENT",
            "Aucun composant déployable n'a été confirmé pendant l'analyse.",
            409,
        )

    return proposals, questions, warnings


def _find_ai_service(
    context: ProposalContext,
    requested_connection_id: int | None,
) -> dict[str, Any] | None:
    candidates = [item for item in context.services if item["service_role"] == "ai_provider"]
    if requested_connection_id is not None:
        for item in candidates:
            if int(item["connection_id"]) == requested_connection_id:
                return item
        raise ProposalError(
            "AI_CONNECTION_NOT_IN_ENVIRONMENT",
            "Le provider IA sélectionné n'appartient pas à l'environnement du projet.",
        )
    return candidates[0] if candidates else None


def _ai_url(service: dict[str, Any]) -> str:
    base_url = _safe_text(service.get("base_url"), maximum=1000).rstrip("/")
    provider_type = _safe_text(service.get("provider_type")).lower()
    if provider_type == "ollama":
        if base_url.endswith("/api"):
            return f"{base_url}/chat"
        return f"{base_url}/api/chat"
    if base_url.endswith("/v1"):
        return f"{base_url}/chat/completions"
    if base_url.endswith("/chat/completions"):
        return base_url
    return f"{base_url}/v1/chat/completions"


def _extract_json_text(value: str) -> dict[str, Any]:
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ProposalError("AI_INVALID_JSON", "Le provider IA n'a pas retourné un JSON valide.", 502)
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError as error:
            raise ProposalError("AI_INVALID_JSON", "Le provider IA n'a pas retourné un JSON valide.", 502) from error
    if not isinstance(parsed, dict):
        raise ProposalError("AI_INVALID_RESPONSE", "La réponse IA doit être un objet JSON.", 502)
    return parsed


def _call_ai(
    *,
    context: ProposalContext,
    service: dict[str, Any],
    model: str,
    decisions: dict[str, Any],
    defaults: list[dict[str, Any]],
) -> dict[str, Any]:
    prompt_context = {
        "project": {
            "name": context.project["name"],
            "slug": context.project["slug"],
            "commitSha": context.analysis.get("analyzed_commit_sha"),
        },
        "environment": {
            "type": context.environment["environment_type"],
            "namespace": decisions["namespace"],
            "domain": decisions.get("domain"),
            "availableRoles": [item["service_role"] for item in context.services],
        },
        "decisions": decisions,
        "components": [
            {
                "id": item["id"],
                "name": item["name"],
                "type": item.get("component_type"),
                "rootPath": item.get("root_path"),
                "runtime": item.get("runtime"),
                "framework": item.get("framework"),
                "packageManager": item.get("package_manager"),
                "buildCommand": item.get("build_command"),
                "startCommand": item.get("start_command"),
                "detectedPort": item.get("detected_port"),
                "dockerfilePath": item.get("dockerfile_path"),
                "helmChartPath": item.get("helm_chart_path"),
                "confidence": item.get("confidence"),
            }
            for item in context.components
            if bool(item.get("deployable", True))
        ],
        "safeDefaults": defaults,
    }
    system_message = (
        "Tu es l'assistant DevOps contrôlé de SApixi. "
        "Retourne uniquement un objet JSON. Ne retourne jamais de secret. "
        "Tu proposes une configuration prudente; SApixi la validera et la générera ensuite. "
        "Le JSON attendu contient components (liste) et warnings (liste). "
        "Chaque composant peut contenir componentId, summary, docker, kubernetes, persistence, migration."
    )
    user_message = json.dumps(prompt_context, ensure_ascii=False)

    headers = {"Content-Type": "application/json"}
    auth = None
    ciphertext = service.get("secret_ciphertext")
    secret = decrypt_credential(ciphertext) if ciphertext else None
    auth_type = _safe_text(service.get("auth_type"), "none").lower()
    if auth_type == "token" and secret:
        headers["Authorization"] = f"Bearer {secret}"
    elif auth_type == "basic" and secret:
        auth = (service.get("username") or "", secret)

    provider_type = _safe_text(service.get("provider_type")).lower()
    if provider_type == "ollama":
        body = {
            "model": model,
            "stream": False,
            "think": False,
            "keep_alive": "30m",
            "format": "json",
            "messages": [
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message},
            ],
            "options": {"temperature": 0.1, "num_predict": 800},
        }
    else:
        body = {
            "model": model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message},
            ],
            "response_format": {"type": "json_object"},
        }

    try:
        response = requests.post(
            _ai_url(service),
            json=body,
            headers=headers,
            auth=auth,
            timeout=int(current_app.config.get("AI_REQUEST_TIMEOUT_SECONDS", 90)),
            verify=bool(service.get("verify_ssl", True)),
        )
    except requests.RequestException as error:
        raise ProposalError(
            "AI_CONNECTION_FAILED",
            "Le provider IA est inaccessible. Une proposition standard sera utilisée.",
            502,
        ) from error

    if response.status_code >= 400:
        raise ProposalError(
            "AI_REQUEST_FAILED",
            f"Le provider IA a répondu avec le code HTTP {response.status_code}.",
            502,
        )

    try:
        payload = response.json()
    except ValueError as error:
        raise ProposalError("AI_INVALID_RESPONSE", "La réponse IA n'est pas du JSON.", 502) from error

    if provider_type == "ollama":
        content = payload.get("message", {}).get("content") or payload.get("response")
    else:
        choices = payload.get("choices") or []
        content = choices[0].get("message", {}).get("content") if choices else None
    if not isinstance(content, str) or not content.strip():
        raise ProposalError("AI_EMPTY_RESPONSE", "Le provider IA a retourné une réponse vide.", 502)
    return _extract_json_text(content)


def _merge_ai_components(
    defaults: list[dict[str, Any]],
    ai_result: dict[str, Any],
    decisions: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    ai_components = ai_result.get("components")
    if not isinstance(ai_components, list):
        return defaults, ["La réponse IA ne contient pas de liste de composants exploitable."]

    by_id: dict[int, dict[str, Any]] = {}
    for item in ai_components:
        if not isinstance(item, dict):
            continue
        try:
            component_id = int(item.get("componentId"))
        except (TypeError, ValueError):
            continue
        by_id[component_id] = item

    merged: list[dict[str, Any]] = []
    for base in defaults:
        suggestion = by_id.get(int(base["componentId"]), {})
        result = json.loads(json.dumps(base))
        result["summary"] = _safe_text(suggestion.get("summary"), result["summary"], 800)

        docker = suggestion.get("docker") if isinstance(suggestion.get("docker"), dict) else {}
        for key in ("strategy", "installCommand", "buildCommand", "startCommand"):
            proposed = _safe_text(docker.get(key), maximum=1000)
            if proposed:
                result["docker"][key] = proposed
        result["docker"]["port"] = _safe_int(
            docker.get("port"), result["docker"]["port"], 1, 65535
        )

        kubernetes = suggestion.get("kubernetes") if isinstance(suggestion.get("kubernetes"), dict) else {}
        service_type = _safe_text(kubernetes.get("serviceType"), result["kubernetes"]["serviceType"], 30)
        if service_type in {"ClusterIP", "NodePort", "LoadBalancer"}:
            result["kubernetes"]["serviceType"] = service_type
        for key in ("readinessPath", "livenessPath", "cpuRequest", "cpuLimit", "memoryRequest", "memoryLimit"):
            proposed = _safe_text(kubernetes.get(key), maximum=100)
            if proposed:
                result["kubernetes"][key] = proposed

        # Les décisions utilisateur restent prioritaires.
        result["kubernetes"]["replicas"] = decisions["replicas"]
        result["kubernetes"]["ingressEnabled"] = (
            decisions["exposureMode"] == "public" and bool(result["kubernetes"].get("host"))
        )

        persistence = suggestion.get("persistence") if isinstance(suggestion.get("persistence"), dict) else {}
        if decisions["persistence"] == "required":
            result["persistence"]["enabled"] = True
            result["persistence"]["mountPath"] = _safe_text(
                persistence.get("mountPath"), result["persistence"].get("mountPath") or "/data", 300
            )
            result["persistence"]["size"] = _safe_text(
                persistence.get("size"), result["persistence"].get("size") or "5Gi", 30
            )
        elif decisions["persistence"] == "none":
            result["persistence"] = {"enabled": False, "mountPath": None, "size": None}

        migration = suggestion.get("migration") if isinstance(suggestion.get("migration"), dict) else {}
        proposed_command = _safe_text(migration.get("command"), maximum=1000)
        if proposed_command and not result["migration"].get("command"):
            result["migration"]["command"] = proposed_command
        if decisions["migration"] == "enabled":
            result["migration"]["enabled"] = bool(result["migration"].get("command"))
        elif decisions["migration"] == "disabled":
            result["migration"]["enabled"] = False

        warnings = suggestion.get("warnings") if isinstance(suggestion.get("warnings"), list) else []
        result["warnings"] = list(dict.fromkeys(result["warnings"] + [_safe_text(item, maximum=400) for item in warnings if _safe_text(item)]))
        merged.append(result)

    ai_warnings = ai_result.get("warnings") if isinstance(ai_result.get("warnings"), list) else []
    return merged, [_safe_text(item, maximum=500) for item in ai_warnings if _safe_text(item)]


def _apply_answers(
    questions: list[dict[str, Any]],
    answers: dict[str, Any],
    components: list[dict[str, Any]],
    decisions: dict[str, Any],
) -> None:
    by_component = {int(item["componentId"]): item for item in components}
    for question in questions:
        answer = answers.get(question["id"], question.get("answer"))
        if answer is not None:
            question["answer"] = _safe_text(answer, maximum=1000)
        if question["id"] == "public-domain" and question.get("answer"):
            decisions["domain"] = _normalize_domain(question["answer"])
            for component in components:
                component["kubernetes"]["host"] = decisions["domain"]
                component["kubernetes"]["ingressEnabled"] = True
        if question["id"].startswith("migration-") and question.get("componentId"):
            component = by_component.get(int(question["componentId"]))
            if component and question.get("answer") in {"enabled", "disabled"}:
                component["migration"]["enabled"] = question["answer"] == "enabled"
                component["migration"]["requiresConfirmation"] = False


def _validation_report(
    *,
    decisions: dict[str, Any],
    questions: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warning_items = [
        {"path": "proposal", "code": "PROPOSAL_WARNING", "message": item}
        for item in warnings
    ]
    question_items: list[dict[str, str]] = []

    if decisions["exposureMode"] == "public" and not decisions.get("domain"):
        errors.append(
            {
                "path": "decisions.domain",
                "code": "DOMAIN_REQUIRED",
                "message": "Un domaine est requis pour une exposition publique.",
            }
        )
    for item in questions:
        if item.get("required") and not _safe_text(item.get("answer")):
            question_items.append(
                {
                    "path": f"questions.{item['id']}",
                    "code": "ANSWER_REQUIRED",
                    "message": item["label"],
                }
            )

    return {
        "valid": len(errors) == 0,
        "errorCount": len(errors),
        "warningCount": len(warning_items),
        "questionCount": len(question_items),
        "errors": errors,
        "warnings": warning_items,
        "questions": question_items,
    }


def _proposal_status(validation: dict[str, Any]) -> str:
    if validation["errorCount"] > 0 or validation["questionCount"] > 0:
        return "needs_input"
    return "ready"


def _save_new_proposal(
    *,
    context: ProposalContext,
    mode: str,
    ai_connection_id: int | None,
    ai_model: str | None,
    decisions: dict[str, Any],
    components: list[dict[str, Any]],
    questions: list[dict[str, Any]],
    warnings: list[str],
    validation: dict[str, Any],
    ai_raw_response: dict[str, Any] | None,
    user_id: int,
) -> dict[str, Any]:
    status = _proposal_status(validation)
    with get_database_connection() as connection:
        connection.execute(
            """
                UPDATE project_deployment_proposals
                SET status = 'failed',
                    updated_at = CURRENT_TIMESTAMP
                WHERE project_id = %s
                  AND status IN ('preparing', 'needs_input', 'ready');
            """,
            (context.project["id"],),
        )
        row = connection.execute(
            """
                INSERT INTO project_deployment_proposals (
                    project_id,
                    analysis_run_id,
                    environment_id,
                    status,
                    mode,
                    ai_connection_id,
                    ai_model,
                    decisions,
                    components,
                    questions,
                    answers,
                    warnings,
                    validation,
                    ai_raw_response,
                    created_by,
                    updated_by
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s::JSONB, %s::JSONB, %s::JSONB, '{}'::JSONB,
                    %s::JSONB, %s::JSONB, %s::JSONB, %s, %s
                )
                RETURNING *;
            """,
            (
                context.project["id"],
                context.analysis["id"],
                context.environment["id"],
                status,
                mode,
                ai_connection_id,
                ai_model,
                json.dumps(decisions),
                json.dumps(components),
                json.dumps(questions),
                json.dumps(warnings),
                json.dumps(validation),
                json.dumps(ai_raw_response or {}),
                user_id,
                user_id,
            ),
        ).fetchone()
        if row is None:
            raise ProposalError("PROPOSAL_SAVE_FAILED", "La proposition n'a pas pu être enregistrée.", 500)
    return dict(row)


def _find_proposal(project_id: int, proposal_id: int | None = None) -> dict[str, Any] | None:
    condition = "proposal.project_id = %s"
    params: list[Any] = [project_id]
    if proposal_id is not None:
        condition += " AND proposal.id = %s"
        params.append(proposal_id)
    with get_database_connection() as connection:
        return connection.execute(
            f"""
                SELECT proposal.*
                FROM project_deployment_proposals AS proposal
                WHERE {condition}
                ORDER BY proposal.created_at DESC
                LIMIT 1;
            """,
            tuple(params),
        ).fetchone()


def _proposal_json(row: dict[str, Any], context: ProposalContext) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "projectId": int(row["project_id"]),
        "analysisRunId": int(row["analysis_run_id"]),
        "environmentId": int(row["environment_id"]),
        "contractId": int(row["contract_id"]) if row.get("contract_id") else None,
        "status": row["status"],
        "mode": row["mode"],
        "aiConnectionId": int(row["ai_connection_id"]) if row.get("ai_connection_id") else None,
        "aiModel": row.get("ai_model"),
        "decisions": _json_value(row.get("decisions"), {}),
        "environment": _environment_json(context),
        "components": _json_value(row.get("components"), []),
        "questions": _json_value(row.get("questions"), []),
        "warnings": _json_value(row.get("warnings"), []),
        "validation": _json_value(row.get("validation"), {
            "valid": False,
            "errorCount": 1,
            "warningCount": 0,
            "questionCount": 0,
            "errors": [],
            "warnings": [],
            "questions": [],
        }),
        "createdAt": _iso(row.get("created_at")),
        "updatedAt": _iso(row.get("updated_at")),
        "confirmedAt": _iso(row.get("confirmed_at")),
    }


def _service_by_role(context: ProposalContext, role: str) -> dict[str, Any] | None:
    return next((item for item in context.services if item["service_role"] == role), None)


def _registry_host(base_url: str) -> str:
    parsed = urlparse(base_url)
    return parsed.netloc or parsed.path.rstrip("/")


def _environment_variables(component: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    values = _json_value(component.get("environment_variables"), [])
    configuration: list[dict[str, Any]] = []
    secrets: list[dict[str, Any]] = []
    for item in values:
        if isinstance(item, str):
            name = item
            required = True
            description = "Variable détectée pendant l'analyse."
        elif isinstance(item, dict):
            name = _safe_text(item.get("name") or item.get("key"), maximum=200)
            required = bool(item.get("required", True))
            description = _safe_text(item.get("description"), "Variable détectée pendant l'analyse.", 500)
        else:
            continue
        if not name:
            continue
        target = secrets if any(marker in name.upper() for marker in SENSITIVE_ENV_MARKERS) else configuration
        target.append({"name": name, "required": required, "description": description})
    return configuration, secrets


def _contract_registry_target(
    context: ProposalContext,
    decisions: dict[str, Any],
    registry_service: dict[str, Any],
) -> dict[str, Any]:
    options = _repository_options(context)["docker"]
    selected = _find_repository(options, name=decisions.get("imageRepositoryName"))
    if selected is None:
        raise ProposalError("DOCKER_REPOSITORY_REQUIRED", "Le repository Docker sélectionné est introuvable.", 409)
    endpoint_url = str(selected.get("endpointUrl") or "").rstrip("/")
    host = _registry_host(endpoint_url)
    if not host:
        raise ProposalError("DOCKER_REPOSITORY_ENDPOINT_MISSING", "L'endpoint Docker du repository est absent.", 409)
    return {
        "connectionId": int(registry_service["connection_id"]),
        "repositoryName": selected.get("name"),
        "repositoryUrl": selected.get("url"),
        "endpointUrl": endpoint_url,
        "host": host,
        "repositoryPrefix": context.project["slug"],
        "imagePullSecretName": "registry-credentials",
    }


def _contract_delivery_target(
    context: ProposalContext,
    decisions: dict[str, Any],
    registry_service: dict[str, Any],
    gitops_service: dict[str, Any] | None,
) -> dict[str, Any]:
    mode = decisions.get("deliveryMode") or "git"
    options = _repository_options(context)
    base_path = "projects"

    if mode == "git":
        selected = _find_repository(options["git"], repository_id=decisions.get("gitRepositoryId"))
        if selected is None or gitops_service is None:
            raise ProposalError("GITOPS_REPOSITORY_REQUIRED", "Le repository GitOps sélectionné est introuvable.", 409)
        return {
            "mode": "git",
            "connectionId": int(gitops_service["connection_id"]),
            "repositoryId": selected.get("projectId") or selected.get("id"),
            "repositoryName": selected.get("name"),
            "repositoryUrl": selected.get("url"),
            "targetRevision": decisions.get("gitBranch") or selected.get("defaultBranch") or "main",
            "basePath": base_path,
            "refreshMode": decisions.get("gitRefreshMode") or "polling",
        }

    selected = _find_repository(options["helm"], name=decisions.get("helmRepositoryName"))
    if selected is None:
        raise ProposalError("HELM_REPOSITORY_REQUIRED", "Le repository Helm sélectionné est introuvable.", 409)
    return {
        "mode": "helm",
        "connectionId": int(registry_service["connection_id"]),
        "repositoryName": selected.get("name"),
        "repositoryUrl": selected.get("url"),
        "targetRevision": "__SAPIXI_HELM_VERSION__",
        "basePath": base_path,
        "refreshMode": "polling",
    }


def _contract_from_proposal(
    *,
    context: ProposalContext,
    proposal: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    decisions = _json_value(proposal.get("decisions"), {})
    proposal_components = _json_value(proposal.get("components"), [])
    analysis_components = {int(item["id"]): item for item in context.components}

    kubernetes = _service_by_role(context, "kubernetes")
    registry = _service_by_role(context, "container_registry")
    gitops = _service_by_role(context, "gitops_repository")
    argocd = _service_by_role(context, "argocd")
    missing = [
        role
        for role, value in (
            ("kubernetes", kubernetes),
            ("container_registry", registry),
            ("argocd", argocd),
        )
        if value is None
    ]
    if decisions.get("deliveryMode") == "git" and gitops is None:
        missing.append("gitops_repository")
    if missing:
        raise ProposalError(
            "ENVIRONMENT_INCOMPLETE",
            "L'environnement ne contient pas : " + ", ".join(missing),
            409,
        )

    contract_components: list[dict[str, Any]] = []
    for item in proposal_components:
        component_id = int(item["componentId"])
        source = analysis_components.get(component_id)
        if source is None:
            continue
        runtime_name, runtime_version = _runtime_parts(source.get("runtime"))
        configuration, secrets = _environment_variables(source)
        docker = item["docker"]
        kube = item["kubernetes"]
        persistence = item["persistence"]
        migration = item["migration"]
        port = _safe_int(docker.get("port"), 8080, 1, 65535)
        service_enabled = _safe_text(source.get("component_type")).lower() not in {"worker", "job", "library"}

        def probe(path: str | None, enabled: bool) -> dict[str, Any]:
            return {
                "enabled": enabled and bool(path),
                "path": path or "/",
                "initialDelaySeconds": 10,
                "periodSeconds": 10,
                "timeoutSeconds": 3,
                "failureThreshold": 6,
            }

        volumes = []
        if persistence.get("enabled"):
            volumes.append(
                {
                    "name": "data",
                    "mountPath": persistence.get("mountPath") or "/data",
                    "size": persistence.get("size") or "5Gi",
                    "accessMode": "ReadWriteOnce",
                    "storageClass": "",
                    "readOnly": False,
                }
            )

        contract_components.append(
            {
                "id": component_id,
                "name": source["name"],
                "slug": _slug(source["name"]),
                "rootPath": source.get("root_path") or ".",
                "componentType": source.get("component_type") or "unknown",
                "runtime": {"name": runtime_name, "version": runtime_version},
                "framework": source.get("framework") or "",
                "packageManager": source.get("package_manager") or "",
                "deployable": bool(source.get("deployable", True)),
                "build": {
                    "context": source.get("root_path") or ".",
                    "dockerfilePath": source.get("dockerfile_path") or "Dockerfile",
                    "helmChartPath": source.get("helm_chart_path") or "",
                    "installCommand": docker.get("installCommand") or "",
                    "buildCommand": docker.get("buildCommand") or "",
                    "outputPath": "",
                },
                "container": {
                    "startCommand": docker.get("startCommand") or "",
                    "port": port,
                    "workingDirectory": "/app",
                    "runAsUser": 10001,
                    "readOnlyRootFilesystem": False,
                },
                "replicas": _safe_int(kube.get("replicas"), decisions.get("replicas", 1), 1, 20),
                "service": {
                    "enabled": service_enabled,
                    "type": kube.get("serviceType") if kube.get("serviceType") in {"ClusterIP", "NodePort", "LoadBalancer"} else "ClusterIP",
                    "port": port,
                    "targetPort": port,
                },
                "ingress": {
                    "enabled": bool(kube.get("ingressEnabled")),
                    "className": "nginx",
                    "host": kube.get("host") or "",
                    "path": "/",
                    "pathType": "Prefix",
                    "tlsSecretName": "",
                    "annotations": {},
                },
                "resources": {
                    "requests": {
                        "cpu": kube.get("cpuRequest") or "100m",
                        "memory": kube.get("memoryRequest") or "128Mi",
                    },
                    "limits": {
                        "cpu": kube.get("cpuLimit") or "500m",
                        "memory": kube.get("memoryLimit") or "512Mi",
                    },
                },
                "probes": {
                    "startup": probe(kube.get("readinessPath"), service_enabled),
                    "readiness": probe(kube.get("readinessPath"), service_enabled),
                    "liveness": probe(kube.get("livenessPath"), service_enabled),
                },
                "configuration": configuration,
                "secrets": secrets,
                "volumes": volumes,
                "migration": {
                    "enabled": bool(migration.get("enabled") and migration.get("command")),
                    "command": migration.get("command") or "",
                    "backoffLimit": 1,
                },
                "dependencies": [],
            }
        )

    environment_code = context.environment["code"]
    contract = {
        "schemaVersion": 2,
        "project": {
            "id": context.project["id"],
            "name": context.project["name"],
            "slug": context.project["slug"],
            "analysisRunId": context.analysis["id"],
            "commitSha": context.analysis.get("analyzed_commit_sha") or "",
        },
        "target": {
            "environmentId": context.environment["id"],
            "environmentName": context.environment["name"],
            "environmentCode": environment_code,
            "namespace": decisions["namespace"],
            "domain": decisions.get("domain"),
            "kubernetes": {"server": kubernetes["base_url"]},
            "registry": _contract_registry_target(context, decisions, registry),
            "delivery": _contract_delivery_target(context, decisions, registry, gitops),
            "argocd": {
                "serverUrl": argocd["base_url"],
                "namespace": "argocd",
                "projectName": f"{context.project['slug']}-{environment_code}",
                "automaticSync": False,
                "prune": True,
                "selfHeal": True,
            },
        },
        "policies": {
            "preserveExistingDockerfile": True,
            "preserveExistingHelmChart": True,
            "requireNonRoot": True,
            "allowPrivileged": False,
            "requireManualArgoSync": True,
            "maximumAiContextBytes": 120000,
        },
        "components": contract_components,
    }

    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    if not contract_components:
        errors.append({"path": "components", "code": "NO_COMPONENT", "message": "Aucun composant ne peut être généré."})
    for index, item in enumerate(contract_components):
        if item["service"]["enabled"] and not item["container"]["startCommand"]:
            errors.append({
                "path": f"components.{index}.container.startCommand",
                "code": "START_COMMAND_REQUIRED",
                "message": f"La commande de démarrage de {item['name']} est absente.",
            })
        if item["ingress"]["enabled"] and not item["ingress"]["host"]:
            errors.append({
                "path": f"components.{index}.ingress.host",
                "code": "INGRESS_HOST_REQUIRED",
                "message": f"Le domaine de {item['name']} est absent.",
            })
    validation = {
        "valid": not errors,
        "errorCount": len(errors),
        "warningCount": len(warnings),
        "questionCount": 0,
        "errors": errors,
        "warnings": warnings,
        "questions": [],
    }
    return contract, validation


def _table_columns(connection, table_name: str) -> set[str]:
    rows = connection.execute(
        """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s;
        """,
        (table_name,),
    ).fetchall()
    return {row["column_name"] for row in rows}


def _insert_contract(
    *,
    context: ProposalContext,
    proposal: dict[str, Any],
    contract: dict[str, Any],
    validation: dict[str, Any],
    user_id: int,
) -> int:
    with get_database_connection() as connection:
        columns = _table_columns(connection, "project_deployment_contracts")
        if not columns:
            raise ProposalError(
                "CONTRACT_TABLE_MISSING",
                "La migration 017_project_workflow_v2.sql n'est pas appliquée.",
                500,
            )

        supersede_assignments = ["status = 'superseded'"]
        if "updated_at" in columns:
            supersede_assignments.append("updated_at = CURRENT_TIMESTAMP")
        connection.execute(
            f"""
                UPDATE project_deployment_contracts
                SET {', '.join(supersede_assignments)}
                WHERE project_id = %s
                  AND status = 'confirmed';
            """,
            (context.project["id"],),
        )

        if "revision" in columns:
            revision_row = connection.execute(
                """
                    SELECT COALESCE(MAX(revision), 0) + 1 AS next_revision
                    FROM project_deployment_contracts
                    WHERE project_id = %s;
                """,
                (context.project["id"],),
            ).fetchone()
            revision = int(revision_row["next_revision"] if revision_row else 1)
        else:
            revision = 1

        values_by_column: dict[str, Any] = {
            "project_id": context.project["id"],
            "analysis_run_id": context.analysis["id"],
            "environment_id": context.environment["id"],
            "proposal_id": proposal["id"],
            "status": "confirmed",
            "revision": revision,
            "namespace": contract["target"]["namespace"],
            "domain": contract["target"].get("domain"),
            "contract": json.dumps(contract),
            "contract_json": json.dumps(contract),
            "validation": json.dumps(validation),
            "validation_json": json.dumps(validation),
            "created_by": user_id,
            "updated_by": user_id,
            "confirmed_by": user_id,
        }
        ordered = [
            name
            for name in (
                "project_id",
                "analysis_run_id",
                "environment_id",
                "proposal_id",
                "status",
                "revision",
                "namespace",
                "domain",
                "contract",
                "contract_json",
                "validation",
                "validation_json",
                "created_by",
                "updated_by",
                "confirmed_by",
            )
            if name in columns
        ]
        required = {"project_id", "analysis_run_id", "environment_id", "status"}
        if not required.issubset(set(ordered)):
            raise ProposalError(
                "CONTRACT_SCHEMA_INCOMPATIBLE",
                "La table project_deployment_contracts ne correspond pas au workflow attendu.",
                500,
            )
        placeholders = [
            "%s::JSONB" if name in {"contract", "contract_json", "validation", "validation_json"} else "%s"
            for name in ordered
        ]
        row = connection.execute(
            f"""
                INSERT INTO project_deployment_contracts ({', '.join(ordered)})
                VALUES ({', '.join(placeholders)})
                RETURNING id;
            """,
            tuple(values_by_column[name] for name in ordered),
        ).fetchone()
        if row is None:
            raise ProposalError("CONTRACT_SAVE_FAILED", "Le contrat interne n'a pas pu être créé.", 500)
        contract_id = int(row["id"])

        updates = [
            "status = 'confirmed'",
            "contract_id = %s",
            "confirmed_by = %s",
            "confirmed_at = CURRENT_TIMESTAMP",
            "updated_by = %s",
            "updated_at = CURRENT_TIMESTAMP",
        ]
        connection.execute(
            f"""
                UPDATE project_deployment_proposals
                SET {', '.join(updates)}
                WHERE id = %s;
            """,
            (contract_id, user_id, user_id, proposal["id"]),
        )

        project_columns = _table_columns(connection, "projects")
        project_updates = ["updated_at = CURRENT_TIMESTAMP"]
        if "deployment_contract_status" in project_columns:
            project_updates.append("deployment_contract_status = 'confirmed'")
        if "latest_deployment_contract_id" in project_columns:
            project_updates.append(f"latest_deployment_contract_id = {contract_id}")
        connection.execute(
            f"UPDATE projects SET {', '.join(project_updates)} WHERE id = %s;",
            (context.project["id"],),
        )

    return contract_id


@projects_blueprint.get("/<int:project_id>/deployment-target-options")
@require_auth
@require_project_access
def deployment_target_options_route(project_id: int):
    try:
        context = _load_context(project_id)
        options = _repository_options(context)
        nexus = _service_by_role(context, "container_registry")
        gitlab = _service_by_role(context, "gitops_repository")
        return jsonify(
            {
                "success": True,
                "data": {
                    "imageRepositories": options["docker"],
                    "helmRepositories": options["helm"],
                    "gitRepositories": options["git"],
                    "nexusConnection": (
                        {
                            "id": int(nexus["connection_id"]),
                            "name": nexus["connection_name"],
                            "status": nexus["status"],
                        }
                        if nexus else None
                    ),
                    "gitConnection": (
                        {
                            "id": int(gitlab["connection_id"]),
                            "name": gitlab["connection_name"],
                            "status": gitlab["status"],
                        }
                        if gitlab else None
                    ),
                },
            }
        )
    except ProposalError as error:
        return error_response(error.code, error.message, error.http_status)


@projects_blueprint.get("/<int:project_id>/deployment-proposals/latest")
@require_auth
@require_project_access
def latest_deployment_proposal_route(project_id: int):
    try:
        context = _load_context(project_id)
        proposal = _find_proposal(project_id)
        return jsonify(
            {
                "success": True,
                "data": {
                    "proposal": _proposal_json(dict(proposal), context) if proposal else None,
                },
            }
        )
    except ProposalError as error:
        if error.code == "ANALYSIS_NOT_CONFIRMED":
            return jsonify({"success": True, "data": {"proposal": None}})
        return error_response(error.code, error.message, error.http_status)


@projects_blueprint.post("/<int:project_id>/deployment-proposals")
@require_auth
@require_project_access
def create_deployment_proposal_route(project_id: int):
    try:
        payload = _json_object()
        context = _load_context(project_id)
        mode = _safe_text(payload.get("mode"), "hybrid", 30)
        if mode not in PROPOSAL_MODES:
            raise ProposalError("INVALID_PROPOSAL_MODE", "Le mode de proposition est invalide.")

        decisions = _validate_decisions(payload.get("decisions"), context)
        defaults, questions, warnings = _build_default_components(context, decisions)
        components = defaults
        ai_raw_response: dict[str, Any] | None = None
        ai_connection_id: int | None = None
        ai_model = _safe_text(payload.get("aiModel"), maximum=200) or None

        requested_ai_id = payload.get("aiConnectionId")
        try:
            requested_ai_id = int(requested_ai_id) if requested_ai_id not in (None, "") else None
        except (TypeError, ValueError) as error:
            raise ProposalError("INVALID_AI_CONNECTION", "Le provider IA sélectionné est invalide.") from error

        if mode == "hybrid":
            ai_service = _find_ai_service(context, requested_ai_id)
            if ai_service is None:
                warnings.append("Aucun provider IA n'est associé à l'environnement. Proposition standard utilisée.")
            elif not ai_model:
                warnings.append("Aucun modèle IA n'est sélectionné. Proposition standard utilisée.")
                ai_connection_id = int(ai_service["connection_id"])
            else:
                ai_connection_id = int(ai_service["connection_id"])
                try:
                    ai_raw_response = _call_ai(
                        context=context,
                        service=ai_service,
                        model=ai_model,
                        decisions=decisions,
                        defaults=defaults,
                    )
                    components, ai_warnings = _merge_ai_components(defaults, ai_raw_response, decisions)
                    warnings.extend(ai_warnings)
                except ProposalError as ai_error:
                    current_app.logger.warning("Proposal AI fallback: %s", ai_error.message)
                    warnings.append(ai_error.message)

        validation = _validation_report(
            decisions=decisions,
            questions=questions,
            warnings=warnings,
        )
        proposal = _save_new_proposal(
            context=context,
            mode=mode,
            ai_connection_id=ai_connection_id,
            ai_model=ai_model,
            decisions=decisions,
            components=components,
            questions=questions,
            warnings=list(dict.fromkeys(warnings)),
            validation=validation,
            ai_raw_response=ai_raw_response,
            user_id=_user_id(),
        )
        return jsonify(
            {
                "success": True,
                "data": {"proposal": _proposal_json(proposal, context)},
            }
        ), 201

    except ProposalError as error:
        return error_response(error.code, error.message, error.http_status)


@projects_blueprint.put("/<int:project_id>/deployment-proposals/<int:proposal_id>")
@require_auth
@require_project_access
def update_deployment_proposal_route(project_id: int, proposal_id: int):
    try:
        payload = _json_object()
        context = _load_context(project_id)
        stored = _find_proposal(project_id, proposal_id)
        if stored is None:
            raise ProposalError("PROPOSAL_NOT_FOUND", "La proposition est introuvable.", 404)
        if stored["status"] == "confirmed":
            raise ProposalError("PROPOSAL_ALREADY_CONFIRMED", "La proposition est déjà confirmée.", 409)

        decisions = _validate_decisions(payload.get("decisions"), context)
        components = _json_value(stored.get("components"), [])
        questions = _json_value(stored.get("questions"), [])
        answers = payload.get("answers") if isinstance(payload.get("answers"), dict) else {}

        # Appliquer les décisions globales sans réinterroger l'IA.
        for component in components:
            component["kubernetes"]["replicas"] = decisions["replicas"]
            component["kubernetes"]["ingressEnabled"] = decisions["exposureMode"] == "public"
            if decisions["exposureMode"] == "public":
                component["kubernetes"]["host"] = decisions.get("domain")
            else:
                component["kubernetes"]["host"] = None
            if decisions["persistence"] == "none":
                component["persistence"] = {"enabled": False, "mountPath": None, "size": None}
            elif decisions["persistence"] == "required":
                component["persistence"]["enabled"] = True
                component["persistence"]["mountPath"] = component["persistence"].get("mountPath") or "/data"
                component["persistence"]["size"] = component["persistence"].get("size") or "5Gi"
            if decisions["migration"] == "disabled":
                component["migration"]["enabled"] = False
                component["migration"]["requiresConfirmation"] = False
            elif decisions["migration"] == "enabled":
                component["migration"]["enabled"] = bool(component["migration"].get("command"))
                component["migration"]["requiresConfirmation"] = False

        _apply_answers(questions, answers, components, decisions)
        warnings = _json_value(stored.get("warnings"), [])
        validation = _validation_report(
            decisions=decisions,
            questions=questions,
            warnings=warnings,
        )
        status = _proposal_status(validation)

        with get_database_connection() as connection:
            row = connection.execute(
                """
                    UPDATE project_deployment_proposals
                    SET decisions = %s::JSONB,
                        components = %s::JSONB,
                        questions = %s::JSONB,
                        answers = COALESCE(answers, '{}'::JSONB) || %s::JSONB,
                        validation = %s::JSONB,
                        status = %s,
                        updated_by = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                      AND project_id = %s
                    RETURNING *;
                """,
                (
                    json.dumps(decisions),
                    json.dumps(components),
                    json.dumps(questions),
                    json.dumps(answers),
                    json.dumps(validation),
                    status,
                    _user_id(),
                    proposal_id,
                    project_id,
                ),
            ).fetchone()
        if row is None:
            raise ProposalError("PROPOSAL_UPDATE_FAILED", "La proposition n'a pas pu être mise à jour.", 500)

        return jsonify(
            {
                "success": True,
                "data": {"proposal": _proposal_json(dict(row), context)},
            }
        )
    except ProposalError as error:
        return error_response(error.code, error.message, error.http_status)


@projects_blueprint.post("/<int:project_id>/deployment-proposals/<int:proposal_id>/confirm")
@require_auth
@require_project_access
def confirm_deployment_proposal_route(project_id: int, proposal_id: int):
    try:
        context = _load_context(project_id)
        proposal = _find_proposal(project_id, proposal_id)
        if proposal is None:
            raise ProposalError("PROPOSAL_NOT_FOUND", "La proposition est introuvable.", 404)
        if proposal["status"] == "confirmed":
            return jsonify(
                {
                    "success": True,
                    "data": {"proposal": _proposal_json(dict(proposal), context)},
                }
            )

        validation = _json_value(proposal.get("validation"), {})
        if proposal["status"] != "ready" or validation.get("errorCount", 1) > 0 or validation.get("questionCount", 1) > 0:
            raise ProposalError(
                "PROPOSAL_NOT_READY",
                "Répondez aux questions et corrigez les erreurs avant la confirmation.",
                409,
            )

        contract, contract_validation = _contract_from_proposal(
            context=context,
            proposal=dict(proposal),
        )
        if not contract_validation["valid"]:
            first_error = contract_validation["errors"][0]["message"] if contract_validation["errors"] else "Le contrat est invalide."
            raise ProposalError("CONTRACT_INVALID", first_error, 409)

        contract_id = _insert_contract(
            context=context,
            proposal=dict(proposal),
            contract=contract,
            validation=contract_validation,
            user_id=_user_id(),
        )
        confirmed = _find_proposal(project_id, proposal_id)
        if confirmed is None:
            raise ProposalError("PROPOSAL_NOT_FOUND", "La proposition confirmée est introuvable.", 500)
        confirmed = dict(confirmed)
        confirmed["contract_id"] = contract_id

        return jsonify(
            {
                "success": True,
                "data": {"proposal": _proposal_json(confirmed, context)},
            }
        )
    except ProposalError as error:
        return error_response(error.code, error.message, error.http_status)