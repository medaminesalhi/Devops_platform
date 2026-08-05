from __future__ import annotations

import os
import socket
import time

from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from flask import current_app
from requests.auth import HTTPBasicAuth
from requests.exceptions import (
    ConnectionError,
    ConnectTimeout,
    ReadTimeout,
    RequestException,
    SSLError,
)


KUBERNETES_SERVICE_ACCOUNT_CA = (
    "/var/run/secrets/kubernetes.io/"
    "serviceaccount/ca.crt"
)


@dataclass
class IntegrationTestResult:
    status: str
    http_status: int | None
    latency_ms: int
    message: str
    checked_url: str | None
    server_reachable: bool
    authenticated: bool | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HttpRequestDefinition:
    url: str
    headers: dict[str, str]
    basic_auth: HTTPBasicAuth | None
    credential_used: bool


class BaseIntegrationAdapter:
    """
    Base commune des intégrations contrôlées en HTTP.
    """

    endpoint_path = ""
    exact_url = False

    def build_request(
        self,
        connection: dict[str, Any],
        credential: str | None,
    ) -> HttpRequestDefinition:
        headers = self.default_headers()

        basic_auth: HTTPBasicAuth | None = None
        credential_used = False

        auth_type = connection.get(
            "auth_type",
            "none",
        )

        if (
            auth_type == "token"
            and credential
        ):
            headers["Authorization"] = (
                f"Bearer {credential}"
            )

            credential_used = True

        elif (
            auth_type == "basic"
            and credential
        ):
            basic_auth = HTTPBasicAuth(
                connection.get("username")
                or "",
                credential,
            )

            credential_used = True

        base_url = str(
            connection.get("base_url")
            or ""
        ).strip()

        checked_url = (
            base_url
            if self.exact_url
            else self.build_url(
                base_url,
                self.endpoint_path,
            )
        )

        return HttpRequestDefinition(
            url=checked_url,
            headers=headers,
            basic_auth=basic_auth,
            credential_used=credential_used,
        )

    def resolve_tls_verification(
        self,
        connection: dict[str, Any],
    ) -> bool | str:
        """
        Kubernetes fournit sa CA dans chaque pod.

        Pour les autres services, on respecte
        le choix verify_ssl enregistré par
        l'utilisateur.
        """

        verify_ssl = bool(
            connection.get(
                "verify_ssl",
                True,
            )
        )

        if (
            connection.get("provider_type")
            == "kubernetes"
            and verify_ssl
            and os.path.isfile(
                KUBERNETES_SERVICE_ACCOUNT_CA
            )
        ):
            return KUBERNETES_SERVICE_ACCOUNT_CA

        return verify_ssl

    def test_connection(
        self,
        connection: dict[str, Any],
        credential: str | None,
    ) -> IntegrationTestResult:
        base_url = str(
            connection.get("base_url")
            or ""
        ).strip()

        if not base_url:
            return IntegrationTestResult(
                status="not_configured",
                http_status=None,
                latency_ms=0,
                message=(
                    "L'adresse du service "
                    "n'est pas configurée."
                ),
                checked_url=None,
                server_reachable=False,
                authenticated=None,
            )

        request_definition = (
            self.build_request(
                connection,
                credential,
            )
        )

        timeout_seconds = int(
            current_app.config[
                "INTEGRATION_TIMEOUT_SECONDS"
            ]
        )

        started_at = time.perf_counter()

        try:
            response = requests.get(
                request_definition.url,
                headers=(
                    request_definition.headers
                ),
                auth=(
                    request_definition.basic_auth
                ),
                timeout=timeout_seconds,
                verify=(
                    self.resolve_tls_verification(
                        connection
                    )
                ),
                allow_redirects=True,
            )

            latency_ms = round(
                (
                    time.perf_counter()
                    - started_at
                )
                * 1000
            )

            status_code = (
                response.status_code
            )

            if 200 <= status_code < 400:
                auth_type = connection.get(
                    "auth_type",
                    "none",
                )

                if (
                    auth_type != "none"
                    and not (
                        request_definition
                        .credential_used
                    )
                ):
                    return IntegrationTestResult(
                        status="degraded",
                        http_status=status_code,
                        latency_ms=latency_ms,
                        message=(
                            "Le serveur répond, "
                            "mais aucun identifiant "
                            "n'est configuré."
                        ),
                        checked_url=(
                            request_definition.url
                        ),
                        server_reachable=True,
                        authenticated=False,
                    )

                return IntegrationTestResult(
                    status="online",
                    http_status=status_code,
                    latency_ms=latency_ms,
                    message=(
                        "Le service est accessible "
                        "et l'authentification "
                        "est valide."
                        if (
                            request_definition
                            .credential_used
                        )
                        else
                        "Le service est accessible."
                    ),
                    checked_url=(
                        request_definition.url
                    ),
                    server_reachable=True,
                    authenticated=(
                        True
                        if (
                            request_definition
                            .credential_used
                        )
                        else None
                    ),
                )

            if status_code in {
                401,
                403,
            }:
                return IntegrationTestResult(
                    status="degraded",
                    http_status=status_code,
                    latency_ms=latency_ms,
                    message=(
                        "Le serveur répond, "
                        "mais l'authentification "
                        "est absente, invalide "
                        "ou insuffisante."
                    ),
                    checked_url=(
                        request_definition.url
                    ),
                    server_reachable=True,
                    authenticated=False,
                )

            if status_code == 404:
                return IntegrationTestResult(
                    status="degraded",
                    http_status=status_code,
                    latency_ms=latency_ms,
                    message=(
                        "Le serveur répond, mais "
                        "l'endpoint de contrôle "
                        "n'existe pas. Vérifiez "
                        "l'adresse de base."
                    ),
                    checked_url=(
                        request_definition.url
                    ),
                    server_reachable=True,
                    authenticated=None,
                )

            return IntegrationTestResult(
                status="offline",
                http_status=status_code,
                latency_ms=latency_ms,
                message=(
                    "Le service a retourné "
                    f"HTTP {status_code}."
                ),
                checked_url=(
                    request_definition.url
                ),
                server_reachable=True,
                authenticated=None,
            )

        except SSLError as error:
            return IntegrationTestResult(
                status="offline",
                http_status=None,
                latency_ms=0,
                message=(
                    "Le certificat TLS n'est pas "
                    "reconnu. Ajoutez la CA du "
                    "service ou désactivez la "
                    "vérification uniquement pour "
                    "un environnement de test : "
                    f"{error}"
                ),
                checked_url=(
                    request_definition.url
                ),
                server_reachable=False,
                authenticated=None,
            )

        except (
            ConnectTimeout,
            ReadTimeout,
        ):
            return IntegrationTestResult(
                status="offline",
                http_status=None,
                latency_ms=(
                    timeout_seconds * 1000
                ),
                message=(
                    "Le service n'a pas répondu "
                    f"en {timeout_seconds} secondes."
                ),
                checked_url=(
                    request_definition.url
                ),
                server_reachable=False,
                authenticated=None,
            )

        except ConnectionError as error:
            return IntegrationTestResult(
                status="offline",
                http_status=None,
                latency_ms=0,
                message=(
                    "Connexion impossible. "
                    "Vérifiez le DNS, le port, "
                    "le routage et le pare-feu : "
                    f"{error}"
                ),
                checked_url=(
                    request_definition.url
                ),
                server_reachable=False,
                authenticated=None,
            )

        except RequestException as error:
            return IntegrationTestResult(
                status="offline",
                http_status=None,
                latency_ms=0,
                message=(
                    "Le contrôle HTTP a échoué : "
                    f"{error}"
                ),
                checked_url=(
                    request_definition.url
                ),
                server_reachable=False,
                authenticated=None,
            )

    def default_headers(
        self,
    ) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "User-Agent": (
                "SApixi-Platform/1.0"
            ),
        }

    def build_url(
        self,
        base_url: str,
        path: str,
    ) -> str:
        return urljoin(
            base_url.rstrip("/") + "/",
            path.lstrip("/"),
        )


class GitLabAdapter(
    BaseIntegrationAdapter
):
    def build_request(
        self,
        connection: dict[str, Any],
        credential: str | None,
    ) -> HttpRequestDefinition:
        headers = self.default_headers()

        auth_type = connection.get(
            "auth_type",
            "none",
        )

        base_url = connection["base_url"]

        if (
            auth_type == "token"
            and credential
        ):
            headers["PRIVATE-TOKEN"] = (
                credential
            )

            return HttpRequestDefinition(
                url=self.build_url(
                    base_url,
                    "/api/v4/user",
                ),
                headers=headers,
                basic_auth=None,
                credential_used=True,
            )

        if (
            auth_type == "basic"
            and credential
        ):
            return HttpRequestDefinition(
                url=self.build_url(
                    base_url,
                    (
                        "/api/v4/projects"
                        "?membership=true"
                        "&per_page=1"
                    ),
                ),
                headers=headers,
                basic_auth=HTTPBasicAuth(
                    connection.get("username")
                    or "",
                    credential,
                ),
                credential_used=True,
            )

        return HttpRequestDefinition(
            url=base_url.rstrip("/") + "/",
            headers=headers,
            basic_auth=None,
            credential_used=False,
        )


class NexusAdapter(
    BaseIntegrationAdapter
):
    def build_request(
        self,
        connection: dict[str, Any],
        credential: str | None,
    ) -> HttpRequestDefinition:
        headers = self.default_headers()

        base_url = connection["base_url"]

        if (
            connection.get("auth_type")
            == "basic"
            and credential
        ):
            return HttpRequestDefinition(
                url=self.build_url(
                    base_url,
                    (
                        "/service/rest/v1/"
                        "repositories"
                    ),
                ),
                headers=headers,
                basic_auth=HTTPBasicAuth(
                    connection.get("username")
                    or "",
                    credential,
                ),
                credential_used=True,
            )

        return HttpRequestDefinition(
            url=self.build_url(
                base_url,
                "/service/rest/v1/status",
            ),
            headers=headers,
            basic_auth=None,
            credential_used=False,
        )


class ArgoCdAdapter(
    BaseIntegrationAdapter
):
    endpoint_path = "/api/version"


class KubernetesAdapter(
    BaseIntegrationAdapter
):
    endpoint_path = "/version"


class OllamaAdapter(
    BaseIntegrationAdapter
):
    endpoint_path = "/api/tags"


class LiteLlmAdapter(
    BaseIntegrationAdapter
):
    endpoint_path = "/health/readiness"


class VllmAdapter(
    BaseIntegrationAdapter
):
    endpoint_path = "/health"


class OpenAiCompatibleAdapter(
    BaseIntegrationAdapter
):
    endpoint_path = "/v1/models"


class GenericHttpAdapter(
    BaseIntegrationAdapter
):
    exact_url = True


class NfsAdapter(
    BaseIntegrationAdapter
):
    """
    Teste l'accessibilité TCP d'un serveur NFS.
    """

    def test_connection(
        self,
        connection: dict[str, Any],
        credential: str | None,
    ) -> IntegrationTestResult:
        del credential

        base_url = str(
            connection.get("base_url")
            or ""
        ).strip()

        parsed = urlparse(base_url)

        if (
            parsed.scheme != "nfs"
            or not parsed.hostname
        ):
            return IntegrationTestResult(
                status="not_configured",
                http_status=None,
                latency_ms=0,
                message=(
                    "Utilisez le format "
                    "nfs://serveur:2049/"
                    "chemin-exporte."
                ),
                checked_url=(
                    base_url or None
                ),
                server_reachable=False,
                authenticated=None,
            )

        host = parsed.hostname
        port = parsed.port or 2049

        timeout_seconds = int(
            current_app.config[
                "INTEGRATION_TIMEOUT_SECONDS"
            ]
        )

        started_at = time.perf_counter()

        try:
            with socket.create_connection(
                (
                    host,
                    port,
                ),
                timeout=timeout_seconds,
            ):
                pass

            latency_ms = round(
                (
                    time.perf_counter()
                    - started_at
                )
                * 1000
            )

            return IntegrationTestResult(
                status="online",
                http_status=None,
                latency_ms=latency_ms,
                message=(
                    "Le serveur NFS répond sur "
                    f"{host}:{port}. "
                    "Ce contrôle vérifie le réseau, "
                    "pas les droits du chemin."
                ),
                checked_url=base_url,
                server_reachable=True,
                authenticated=None,
            )

        except (
            TimeoutError,
            socket.timeout,
        ):
            return IntegrationTestResult(
                status="offline",
                http_status=None,
                latency_ms=(
                    timeout_seconds * 1000
                ),
                message=(
                    "Le serveur NFS "
                    f"{host}:{port} "
                    "n'a pas répondu en "
                    f"{timeout_seconds} secondes."
                ),
                checked_url=base_url,
                server_reachable=False,
                authenticated=None,
            )

        except OSError as error:
            return IntegrationTestResult(
                status="offline",
                http_status=None,
                latency_ms=0,
                message=(
                    "Connexion NFS impossible "
                    f"vers {host}:{port} : "
                    f"{error}"
                ),
                checked_url=base_url,
                server_reachable=False,
                authenticated=None,
            )


ADAPTERS: dict[
    str,
    BaseIntegrationAdapter,
] = {
    "gitlab": GitLabAdapter(),
    "nexus": NexusAdapter(),
    "argocd": ArgoCdAdapter(),
    "kubernetes": KubernetesAdapter(),
    "nfs": NfsAdapter(),
    "ollama": OllamaAdapter(),
    "litellm": LiteLlmAdapter(),
    "vllm": VllmAdapter(),
    "openai_compatible":
        OpenAiCompatibleAdapter(),
    "generic_http":
        GenericHttpAdapter(),
}


def get_adapter(
    provider_type: str,
) -> BaseIntegrationAdapter:
    adapter = ADAPTERS.get(
        provider_type
    )

    if adapter is None:
        raise ValueError(
            "Fournisseur non supporté : "
            f"{provider_type}"
        )

    return adapter