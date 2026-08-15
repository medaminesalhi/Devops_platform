from __future__ import annotations

from typing import Any
from urllib.parse import quote, urljoin, urlparse

import requests
from flask import current_app
from requests.auth import HTTPBasicAuth


class RepositoryDiscoveryError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _timeout() -> int:
    return int(current_app.config.get("INTEGRATION_TIMEOUT_SECONDS", 10))


def _verify(connection: dict[str, Any]) -> bool:
    return bool(connection.get("verify_ssl", True))


def _auth_parts(
    connection: dict[str, Any],
    credential: str | None,
) -> tuple[dict[str, str], HTTPBasicAuth | None]:
    headers = {"Accept": "application/json"}
    basic_auth: HTTPBasicAuth | None = None
    auth_type = str(connection.get("auth_type") or "none")

    if auth_type == "token" and credential:
        if str(connection.get("provider_type") or "") == "gitlab":
            headers["PRIVATE-TOKEN"] = credential
        else:
            headers["Authorization"] = f"Bearer {credential}"
    elif auth_type == "basic" and credential:
        basic_auth = HTTPBasicAuth(str(connection.get("username") or ""), credential)

    return headers, basic_auth


def _get_json(
    connection: dict[str, Any],
    credential: str | None,
    url: str,
    *,
    params: dict[str, Any] | None = None,
) -> tuple[Any, requests.Response]:
    headers, basic_auth = _auth_parts(connection, credential)
    try:
        response = requests.get(
            url,
            headers=headers,
            auth=basic_auth,
            params=params,
            timeout=_timeout(),
            verify=_verify(connection),
            allow_redirects=True,
        )
    except requests.RequestException as error:
        raise RepositoryDiscoveryError(
            f"Impossible d'interroger le fournisseur : {error}"
        ) from error

    if response.status_code in {401, 403}:
        raise RepositoryDiscoveryError(
            "Le fournisseur refuse l'accès aux repositories avec le credential configuré."
        )
    if response.status_code >= 400:
        raise RepositoryDiscoveryError(
            f"Le fournisseur a retourné HTTP {response.status_code}."
        )

    try:
        return response.json(), response
    except ValueError as error:
        raise RepositoryDiscoveryError(
            "Le fournisseur n'a pas retourné une réponse JSON exploitable."
        ) from error


def _probe_endpoint(
    connection: dict[str, Any],
    credential: str | None,
    url: str,
) -> tuple[bool, str | None, int | None]:
    headers, basic_auth = _auth_parts(connection, credential)
    try:
        response = requests.get(
            url,
            headers=headers,
            auth=basic_auth,
            timeout=min(_timeout(), 5),
            verify=_verify(connection),
            allow_redirects=True,
        )
    except requests.RequestException as error:
        return False, str(error), None
    # 401 est une réponse normale de nombreux registries Docker V2 :
    # le endpoint est bien joignable et annonce ensuite son challenge Bearer.
    return response.status_code < 500, None, response.status_code


def _nexus_registry_endpoint(
    base_url: str,
    repository_url: str | None,
    docker_settings: dict[str, Any],
) -> str:
    parsed = urlparse(base_url)
    hostname = parsed.hostname or ""
    http_port = docker_settings.get("httpPort")
    https_port = docker_settings.get("httpsPort")

    if https_port:
        return f"https://{hostname}:{int(https_port)}"
    if http_port:
        return f"http://{hostname}:{int(http_port)}"

    # Nexus >= 3.83 peut utiliser le routage Docker par chemin.
    # Pour les anciennes versions, l'absence de connector sera signalée
    # à l'étape de validation du choix.
    return str(repository_url or "").rstrip("/")


def discover_nexus_repositories(
    connection: dict[str, Any],
    credential: str | None,
) -> list[dict[str, Any]]:
    base_url = str(connection.get("base_url") or "").rstrip("/")
    payload, _ = _get_json(
        connection,
        credential,
        f"{base_url}/service/rest/v1/repositories",
    )
    if not isinstance(payload, list):
        raise RepositoryDiscoveryError("La liste des repositories Nexus est invalide.")

    repositories: list[dict[str, Any]] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        repository_type = str(raw.get("type") or "").strip().lower()
        format_name = str(raw.get("format") or "").strip().lower()
        repository_url = str(raw.get("url") or "").rstrip("/") or None
        if not name:
            continue

        endpoint_url = repository_url
        metadata: dict[str, Any] = {}
        if format_name == "docker":
            try:
                details, _ = _get_json(
                    connection,
                    credential,
                    (
                        f"{base_url}/service/rest/v1/repositories/docker/"
                        f"{quote(repository_type, safe='')}/{quote(name, safe='')}"
                    ),
                )
                if isinstance(details, dict):
                    docker_settings = details.get("docker") or {}
                    if isinstance(docker_settings, dict):
                        metadata["docker"] = docker_settings
                        endpoint_url = _nexus_registry_endpoint(
                            base_url,
                            repository_url,
                            docker_settings,
                        )
            except RepositoryDiscoveryError:
                # Le listing générique reste utile même si l'utilisateur
                # n'a pas accès au détail de configuration.
                pass

        probe_url: str | None = None
        if format_name == "docker" and endpoint_url:
            probe_url = f"{str(endpoint_url).rstrip('/')}/v2/"
        elif format_name == "helm" and repository_url:
            probe_url = f"{repository_url.rstrip('/')}/"

        if probe_url:
            reachable, endpoint_error, endpoint_status = _probe_endpoint(
                connection, credential, probe_url
            )
            metadata["endpointReachable"] = reachable
            metadata["endpointHttpStatus"] = endpoint_status
            if endpoint_error:
                metadata["endpointError"] = endpoint_error

        repositories.append(
            {
                "provider": "nexus",
                "id": name,
                "name": name,
                "label": name,
                "format": format_name,
                "type": repository_type,
                "url": repository_url,
                "endpointUrl": endpoint_url,
                "defaultBranch": None,
                "projectId": None,
                "writable": repository_type == "hosted",
                "metadata": metadata,
            }
        )

    return repositories


def discover_gitlab_repositories(
    connection: dict[str, Any],
    credential: str | None,
) -> list[dict[str, Any]]:
    base_url = str(connection.get("base_url") or "").rstrip("/")
    repositories: list[dict[str, Any]] = []
    page = 1

    while page <= 10:
        payload, response = _get_json(
            connection,
            credential,
            f"{base_url}/api/v4/projects",
            params={
                "membership": "true",
                "simple": "true",
                "per_page": 100,
                "page": page,
                "order_by": "last_activity_at",
                "sort": "desc",
            },
        )
        if not isinstance(payload, list):
            raise RepositoryDiscoveryError("La liste des projets GitLab est invalide.")

        for raw in payload:
            if not isinstance(raw, dict):
                continue
            project_id = raw.get("id")
            path = str(raw.get("path_with_namespace") or raw.get("name") or "").strip()
            repo_url = str(raw.get("http_url_to_repo") or "").strip()
            if not project_id or not path or not repo_url:
                continue
            repositories.append(
                {
                    "provider": "gitlab",
                    "id": str(project_id),
                    "name": path,
                    "label": path,
                    "format": "git",
                    "type": "project",
                    "url": repo_url,
                    "endpointUrl": repo_url,
                    "defaultBranch": raw.get("default_branch") or "main",
                    "projectId": int(project_id),
                    "writable": True,
                    "metadata": {
                        "webUrl": raw.get("web_url"),
                        "visibility": raw.get("visibility"),
                    },
                }
            )

        next_page = str(response.headers.get("X-Next-Page") or "").strip()
        if not next_page:
            break
        try:
            page = int(next_page)
        except ValueError:
            break

    return repositories


def resolve_gitlab_repository(
    connection: dict[str, Any],
    credential: str | None,
    repository_ref: str,
) -> dict[str, Any]:
    """Résout un projet GitLab saisi manuellement par ID, chemin ou URL.

    Exemples acceptés :
      - 123
      - groupe/projet
      - https://gitlab.exemple.local/groupe/projet
      - https://gitlab.exemple.local/groupe/projet.git

    Le projet est toujours vérifié auprès du GitLab configuré. On ne fait
    donc pas confiance aveuglément à une URL fournie par le navigateur.
    """

    base_url = str(connection.get("base_url") or "").rstrip("/")
    raw = str(repository_ref or "").strip()
    if not raw:
        raise RepositoryDiscoveryError("Le repository GitLab est vide.")

    base = urlparse(base_url)
    identifier = raw

    if "://" in raw:
        parsed = urlparse(raw)
        if (
            parsed.hostname
            and base.hostname
            and parsed.hostname.lower() != base.hostname.lower()
        ):
            raise RepositoryDiscoveryError(
                "L'URL du repository n'appartient pas au GitLab configuré."
            )

        path = parsed.path.strip("/")
        base_path = base.path.strip("/")
        if base_path and path.startswith(base_path + "/"):
            path = path[len(base_path) + 1 :]
        identifier = path

    identifier = identifier.strip("/")
    if identifier.endswith(".git"):
        identifier = identifier[:-4]
    if not identifier:
        raise RepositoryDiscoveryError("Le repository GitLab est invalide.")

    payload, _ = _get_json(
        connection,
        credential,
        f"{base_url}/api/v4/projects/{quote(identifier, safe='')}",
    )
    if not isinstance(payload, dict):
        raise RepositoryDiscoveryError("Le projet GitLab retourné est invalide.")

    project_id = payload.get("id")
    path = str(
        payload.get("path_with_namespace")
        or payload.get("name")
        or ""
    ).strip()
    repo_url = str(payload.get("http_url_to_repo") or "").strip()
    if not project_id or not path or not repo_url:
        raise RepositoryDiscoveryError(
            "Le projet GitLab ne contient pas les informations nécessaires au GitOps."
        )

    permissions = payload.get("permissions") if isinstance(payload.get("permissions"), dict) else {}
    access_levels: list[int] = []
    for key in ("project_access", "group_access"):
        value = permissions.get(key) if isinstance(permissions, dict) else None
        if isinstance(value, dict):
            try:
                access_levels.append(int(value.get("access_level") or 0))
            except (TypeError, ValueError):
                pass

    # GitLab Developer = 30. Si le serveur ne renvoie pas `permissions`,
    # on laisse la validation du push au déploiement plutôt que de bloquer
    # un projet pourtant accessible.
    writable = max(access_levels, default=30) >= 30

    return {
        "provider": "gitlab",
        "id": str(project_id),
        "name": path,
        "label": path,
        "format": "git",
        "type": "project",
        "url": repo_url,
        "endpointUrl": repo_url,
        "defaultBranch": payload.get("default_branch") or "main",
        "projectId": int(project_id),
        "writable": writable,
        "metadata": {
            "webUrl": payload.get("web_url"),
            "visibility": payload.get("visibility"),
            "manual": True,
        },
    }


def discover_repositories(
    connection: dict[str, Any],
    credential: str | None,
) -> list[dict[str, Any]]:
    provider_type = str(connection.get("provider_type") or "").lower()
    if provider_type == "nexus":
        return discover_nexus_repositories(connection, credential)
    if provider_type == "gitlab":
        return discover_gitlab_repositories(connection, credential)
    return []
