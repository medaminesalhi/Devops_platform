from __future__ import annotations

import time

from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urljoin

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


class BaseIntegrationAdapter:
    """
    Classe commune à tous les adaptateurs.

    Chaque fournisseur doit construire :
    - l'URL de test ;
    - les headers ;
    - l'authentification HTTP éventuelle.
    """

    def build_request(
        self,
        connection: dict[str, Any],
        credential: str | None,
    ) -> tuple[
        str,
        dict[str, str],
        HTTPBasicAuth | None,
        bool,
    ]:
        raise NotImplementedError


    def test_connection(
        self,
        connection: dict[str, Any],
        credential: str | None,
    ) -> IntegrationTestResult:
        base_url = (
            connection.get("base_url")
            or ""
        ).strip()

        if not base_url:
            return IntegrationTestResult(
                status="not_configured",
                http_status=None,
                latency_ms=0,
                message=(
                    "L'URL du service "
                    "n'est pas configurée."
                ),
                checked_url=None,
                server_reachable=False,
                authenticated=None,
            )

        (
            checked_url,
            headers,
            basic_auth,
            credential_used,
        ) = self.build_request(
            connection,
            credential,
        )

        timeout_seconds = current_app.config[
            "INTEGRATION_TIMEOUT_SECONDS"
        ]

        started_at = time.perf_counter()

        try:
            response = requests.get(
                checked_url,
                headers=headers,
                auth=basic_auth,
                timeout=timeout_seconds,
                verify=connection.get(
                    "verify_ssl",
                    True,
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

            status_code = response.status_code

            if 200 <= status_code < 400:
                auth_type = connection.get(
                    "auth_type",
                    "none",
                )

                if (
                    auth_type != "none"
                    and not credential_used
                ):
                    return IntegrationTestResult(
                        status="degraded",
                        http_status=status_code,
                        latency_ms=latency_ms,
                        message=(
                            "Le serveur répond, "
                            "mais aucun credential "
                            "n'est configuré."
                        ),
                        checked_url=checked_url,
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
                        if credential_used
                        else
                        "Le service est accessible."
                    ),
                    checked_url=checked_url,
                    server_reachable=True,
                    authenticated=(
                        True
                        if credential_used
                        else None
                    ),
                )

            if status_code in {401, 403}:
                return IntegrationTestResult(
                    status="degraded",
                    http_status=status_code,
                    latency_ms=latency_ms,
                    message=(
                        "Le serveur répond, mais "
                        "l'authentification est "
                        "absente, invalide ou "
                        "insuffisante."
                    ),
                    checked_url=checked_url,
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
                        "n'existe pas."
                    ),
                    checked_url=checked_url,
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
                checked_url=checked_url,
                server_reachable=True,
                authenticated=None,
            )

        except SSLError as error:
            return IntegrationTestResult(
                status="offline",
                http_status=None,
                latency_ms=0,
                message=(
                    "Le certificat SSL "
                    f"n'est pas reconnu : {error}"
                ),
                checked_url=checked_url,
                server_reachable=False,
                authenticated=None,
            )

        except (ConnectTimeout, ReadTimeout):
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
                checked_url=checked_url,
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
                    "Vérifiez le DNS, le port "
                    f"et le pare-feu : {error}"
                ),
                checked_url=checked_url,
                server_reachable=False,
                authenticated=None,
            )

        except RequestException as error:
            return IntegrationTestResult(
                status="offline",
                http_status=None,
                latency_ms=0,
                message=(
                    "Le test HTTP a échoué : "
                    f"{error}"
                ),
                checked_url=checked_url,
                server_reachable=False,
                authenticated=None,
            )


    def default_headers(
        self,
    ) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "User-Agent": (
                "Piximind-Deployment-Platform/1.0"
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


class GitLabAdapter(BaseIntegrationAdapter):
    def build_request(
        self,
        connection: dict[str, Any],
        credential: str | None,
    ) -> tuple[
        str,
        dict[str, str],
        HTTPBasicAuth | None,
        bool,
    ]:
        headers = self.default_headers()

        auth_type = connection.get(
            "auth_type",
            "none",
        )

        base_url = connection["base_url"]

        if auth_type == "token" and credential:
            headers["PRIVATE-TOKEN"] = credential

            return (
                self.build_url(
                    base_url,
                    "/api/v4/user",
                ),
                headers,
                None,
                True,
            )

        if auth_type == "basic" and credential:
            username = connection.get(
                "username"
            )

            return (
                self.build_url(
                    base_url,
                    "/api/v4/projects"
                    "?membership=true&per_page=1",
                ),
                headers,
                HTTPBasicAuth(
                    username or "",
                    credential,
                ),
                True,
            )

        # Sans token, nous testons seulement
        # la page racine du serveur GitLab.
        return (
            base_url.rstrip("/") + "/",
            headers,
            None,
            False,
        )


class NexusAdapter(BaseIntegrationAdapter):
    def build_request(
        self,
        connection: dict[str, Any],
        credential: str | None,
    ) -> tuple[
        str,
        dict[str, str],
        HTTPBasicAuth | None,
        bool,
    ]:
        headers = self.default_headers()

        base_url = connection["base_url"]

        if (
            connection.get("auth_type")
            == "basic"
            and credential
        ):
            return (
                self.build_url(
                    base_url,
                    "/service/rest/v1/repositories",
                ),
                headers,
                HTTPBasicAuth(
                    connection.get("username")
                    or "",
                    credential,
                ),
                True,
            )

        return (
            self.build_url(
                base_url,
                "/service/rest/v1/status",
            ),
            headers,
            None,
            False,
        )


class ArgoCdAdapter(BaseIntegrationAdapter):
    def build_request(
        self,
        connection: dict[str, Any],
        credential: str | None,
    ) -> tuple[
        str,
        dict[str, str],
        HTTPBasicAuth | None,
        bool,
    ]:
        headers = self.default_headers()

        credential_used = False

        if credential:
            headers["Authorization"] = (
                f"Bearer {credential}"
            )

            credential_used = True

        return (
            self.build_url(
                connection["base_url"],
                "/api/version",
            ),
            headers,
            None,
            credential_used,
        )


class KubernetesAdapter(BaseIntegrationAdapter):
    def build_request(
        self,
        connection: dict[str, Any],
        credential: str | None,
    ) -> tuple[
        str,
        dict[str, str],
        HTTPBasicAuth | None,
        bool,
    ]:
        headers = self.default_headers()

        credential_used = False

        if credential:
            headers["Authorization"] = (
                f"Bearer {credential}"
            )

            credential_used = True

        return (
            self.build_url(
                connection["base_url"],
                "/version",
            ),
            headers,
            None,
            credential_used,
        )


class OllamaAdapter(BaseIntegrationAdapter):
    def build_request(
        self,
        connection: dict[str, Any],
        credential: str | None,
    ) -> tuple[
        str,
        dict[str, str],
        HTTPBasicAuth | None,
        bool,
    ]:
        return (
            self.build_url(
                connection["base_url"],
                "/api/tags",
            ),
            self.default_headers(),
            None,
            False,
        )


class GenericHttpAdapter(BaseIntegrationAdapter):
    def build_request(
        self,
        connection: dict[str, Any],
        credential: str | None,
    ) -> tuple[
        str,
        dict[str, str],
        HTTPBasicAuth | None,
        bool,
    ]:
        headers = self.default_headers()

        credential_used = False
        basic_auth = None

        auth_type = connection.get(
            "auth_type",
            "none",
        )

        if auth_type == "token" and credential:
            headers["Authorization"] = (
                f"Bearer {credential}"
            )

            credential_used = True

        elif auth_type == "basic" and credential:
            basic_auth = HTTPBasicAuth(
                connection.get("username")
                or "",
                credential,
            )

            credential_used = True

        return (
            connection["base_url"],
            headers,
            basic_auth,
            credential_used,
        )


ADAPTERS: dict[
    str,
    BaseIntegrationAdapter,
] = {
    "gitlab": GitLabAdapter(),
    "nexus": NexusAdapter(),
    "argocd": ArgoCdAdapter(),
    "kubernetes": KubernetesAdapter(),
    "ollama": OllamaAdapter(),
    "generic_http": GenericHttpAdapter(),
}


def get_adapter(
    provider_type: str,
) -> BaseIntegrationAdapter:
    adapter = ADAPTERS.get(provider_type)

    if adapter is None:
        raise ValueError(
            f"Fournisseur non supporté : "
            f"{provider_type}"
        )

    return adapter