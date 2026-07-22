from __future__ import annotations

import os
import re
import subprocess
import tempfile

from dataclasses import (
    asdict,
    dataclass,
)

from pathlib import Path

from typing import Any

from urllib.parse import (
    quote,
    urlparse,
    urlunparse,
)

from flask import current_app


class SourceProviderError(RuntimeError):
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


@dataclass
class SourceValidationResult:
    repository_url: str
    repository_path: str
    repository_host: str

    branch: str
    commit_sha: str

    visibility: str
    transport: str

    validation_method: str = "git_ls_remote"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GitSourceProvider:
    def validate_repository(
        self,
        *,
        connection: dict[str, Any],
        repository_url: str,
        visibility: str,
        transport: str,
        branch: str,
        username: str | None,
        secret: str | None,
    ) -> SourceValidationResult:
        repository_host = self.extract_host(
            repository_url
        )

        expected_host = (
            connection.get("ssh_host")
            if transport == "ssh"
            else None
        ) or (
            urlparse(
                connection["base_url"]
            ).hostname
            or ""
        )

        if (
            expected_host
            and repository_host.lower()
            != expected_host.lower()
        ):
            raise SourceProviderError(
                "REPOSITORY_HOST_MISMATCH",
                (
                    "L'URL appartient à un autre serveur "
                    "que la connexion GitLab sélectionnée."
                ),
            )

        repository_path = (
            self.extract_repository_path(
                repository_url
            )
        )

        if transport == "https":
            commit_sha = self.run_https(
                repository_url=repository_url,
                branch=branch,
                username=username,
                secret=secret,
                verify_ssl=bool(
                    connection.get(
                        "verify_ssl",
                        True,
                    )
                ),
            )

        else:
            if not secret:
                raise SourceProviderError(
                    "SSH_PRIVATE_KEY_REQUIRED",
                    "La clé privée SSH est absente.",
                )

            commit_sha = self.run_ssh(
                repository_url=repository_url,
                branch=branch,
                private_key=secret,
                host=repository_host,
                port=int(
                    connection.get("ssh_port")
                    or 22
                ),
            )

        return SourceValidationResult(
            repository_url=repository_url,
            repository_path=repository_path,
            repository_host=repository_host,

            branch=branch,
            commit_sha=commit_sha,

            visibility=visibility,
            transport=transport,
        )


    def run_https(
        self,
        *,
        repository_url: str,
        branch: str,
        username: str | None,
        secret: str | None,
        verify_ssl: bool,
    ) -> str:
        environment = os.environ.copy()

        environment["GIT_TERMINAL_PROMPT"] = "0"

        target_url = repository_url

        with tempfile.TemporaryDirectory(
            prefix="piximind-git-https-"
        ) as temporary_directory:
            if secret:
                if not username:
                    raise SourceProviderError(
                        "GIT_USERNAME_REQUIRED",
                        "Le username Git est absent.",
                    )

                askpass_path = (
                    self.create_askpass_script(
                        Path(temporary_directory)
                    )
                )

                target_url = (
                    self.add_username_to_url(
                        repository_url,
                        username,
                    )
                )

                environment["GIT_ASKPASS"] = str(
                    askpass_path
                )

                environment[
                    "PIXIMIND_GIT_USERNAME"
                ] = username

                environment[
                    "PIXIMIND_GIT_SECRET"
                ] = secret

            command = [
                "git",
                "-c",
                "credential.helper=",
            ]

            if not verify_ssl:
                command.extend(
                    [
                        "-c",
                        "http.sslVerify=false",
                    ]
                )

            command.extend(
                [
                    "ls-remote",
                    "--heads",
                    target_url,
                    f"refs/heads/{branch}",
                ]
            )

            return self.run_process(
                command=command,
                environment=environment,
                branch=branch,
            )


    def run_ssh(
        self,
        *,
        repository_url: str,
        branch: str,
        private_key: str,
        host: str,
        port: int,
    ) -> str:
        normalized_key = private_key

        if (
            "\\n" in normalized_key
            and "\n" not in normalized_key
        ):
            normalized_key = (
                normalized_key.replace(
                    "\\n",
                    "\n",
                )
            )

        timeout = self.timeout_seconds()

        with tempfile.TemporaryDirectory(
            prefix="piximind-git-ssh-"
        ) as temporary_directory:
            directory = Path(
                temporary_directory
            )

            key_path = directory / "deploy_key"
            known_hosts_path = directory / "known_hosts"

            key_path.write_text(
                normalized_key.strip() + "\n",
                encoding="utf-8",
            )

            try:
                os.chmod(
                    key_path,
                    0o600,
                )
            except OSError:
                pass

            try:
                scan_result = subprocess.run(
                    [
                        "ssh-keyscan",
                        "-p",
                        str(port),
                        host,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )

            except FileNotFoundError as error:
                raise SourceProviderError(
                    "OPENSSH_NOT_INSTALLED",
                    (
                        "OpenSSH n'est pas installé "
                        "sur la machine Flask."
                    ),
                    500,
                ) from error

            if (
                scan_result.returncode != 0
                or not scan_result.stdout.strip()
            ):
                raise SourceProviderError(
                    "SSH_HOST_SCAN_FAILED",
                    (
                        "Impossible de récupérer "
                        "l'empreinte SSH du serveur GitLab."
                    ),
                    502,
                )

            known_hosts_path.write_text(
                scan_result.stdout,
                encoding="utf-8",
            )

            environment = os.environ.copy()

            environment["GIT_TERMINAL_PROMPT"] = "0"

            environment["GIT_SSH_COMMAND"] = (
                f'ssh -i "{key_path}" '
                "-o IdentitiesOnly=yes "
                "-o StrictHostKeyChecking=yes "
                f'-o UserKnownHostsFile="{known_hosts_path}" '
                f"-p {port}"
            )

            command = [
                "git",
                "-c",
                "credential.helper=",
                "ls-remote",
                "--heads",
                repository_url,
                f"refs/heads/{branch}",
            ]

            return self.run_process(
                command=command,
                environment=environment,
                branch=branch,
            )


    def run_process(
        self,
        *,
        command: list[str],
        environment: dict[str, str],
        branch: str,
    ) -> str:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds(),
                env=environment,
                check=False,
            )

        except FileNotFoundError as error:
            raise SourceProviderError(
                "GIT_NOT_INSTALLED",
                (
                    "Git n'est pas installé "
                    "sur la machine Flask."
                ),
                500,
            ) from error

        except subprocess.TimeoutExpired as error:
            raise SourceProviderError(
                "GIT_TIMEOUT",
                (
                    "Le serveur GitLab n'a pas répondu "
                    "dans le délai autorisé."
                ),
                504,
            ) from error

        if result.returncode != 0:
            self.raise_git_error(
                result.stderr
                or result.stdout
                or ""
            )

        output = result.stdout.strip()

        if not output:
            raise SourceProviderError(
                "GIT_BRANCH_NOT_FOUND",
                (
                    f"La branche « {branch} » "
                    "est introuvable."
                ),
                404,
            )

        commit_sha = (
            output
            .splitlines()[0]
            .split()[0]
            .strip()
        )

        if not commit_sha:
            raise SourceProviderError(
                "GIT_COMMIT_NOT_FOUND",
                "Aucun commit n'a été détecté.",
                502,
            )

        return commit_sha


    def raise_git_error(
        self,
        raw_error: str,
    ) -> None:
        error = raw_error.lower()

        if (
            "authentication failed" in error
            or "access denied" in error
            or "permission denied" in error
            or "http basic" in error
        ):
            raise SourceProviderError(
                "GIT_AUTHENTICATION_FAILED",
                (
                    "Authentification refusée. Vérifiez "
                    "le username, le mot de passe, le token "
                    "ou la clé SSH."
                ),
                401,
            )

        if (
            "repository not found" in error
            or "not found" in error
        ):
            raise SourceProviderError(
                "GIT_REPOSITORY_NOT_FOUND",
                (
                    "Le repository est introuvable "
                    "ou le credential n'y a pas accès."
                ),
                404,
            )

        if (
            "ssl certificate problem" in error
            or "certificate verify failed" in error
        ):
            raise SourceProviderError(
                "GIT_SSL_ERROR",
                (
                    "Le certificat SSL du serveur "
                    "GitLab n'est pas reconnu."
                ),
                502,
            )

        if (
            "could not resolve host" in error
            or "name or service not known" in error
        ):
            raise SourceProviderError(
                "GIT_DNS_ERROR",
                (
                    "Le nom du serveur GitLab "
                    "ne peut pas être résolu."
                ),
                502,
            )

        raise SourceProviderError(
            "GIT_ACCESS_FAILED",
            (
                "Git n'a pas pu accéder au repository. "
                "Vérifiez l'URL et le credential."
            ),
            403,
        )


    def create_askpass_script(
        self,
        directory: Path,
    ) -> Path:
        if os.name == "nt":
            path = directory / "git-askpass.bat"

            path.write_text(
                (
                    "@echo off\r\n"
                    "echo %~1 | findstr /I "
                    "\"Username\" >nul\r\n"
                    "if not errorlevel 1 (\r\n"
                    "  echo %PIXIMIND_GIT_USERNAME%\r\n"
                    ") else (\r\n"
                    "  echo %PIXIMIND_GIT_SECRET%\r\n"
                    ")\r\n"
                ),
                encoding="utf-8",
            )

            return path

        path = directory / "git-askpass.sh"

        path.write_text(
            (
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "  *Username*) "
                "printf '%s\\n' "
                "\"$PIXIMIND_GIT_USERNAME\" ;;\n"
                "  *) "
                "printf '%s\\n' "
                "\"$PIXIMIND_GIT_SECRET\" ;;\n"
                "esac\n"
            ),
            encoding="utf-8",
        )

        os.chmod(path, 0o700)

        return path


    def add_username_to_url(
        self,
        repository_url: str,
        username: str,
    ) -> str:
        parsed = urlparse(
            repository_url
        )

        host = parsed.hostname or ""

        port = (
            f":{parsed.port}"
            if parsed.port
            else ""
        )

        netloc = (
            f"{quote(username, safe='')}"
            f"@{host}{port}"
        )

        return urlunparse(
            parsed._replace(
                netloc=netloc
            )
        )


    def extract_host(
        self,
        repository_url: str,
    ) -> str:
        if repository_url.startswith(
            ("https://", "ssh://")
        ):
            host = urlparse(
                repository_url
            ).hostname

            if host:
                return host

        match = re.match(
            r"^[^@\s]+@(?P<host>[^:\s]+):.+$",
            repository_url,
        )

        if match:
            return match.group("host")

        raise SourceProviderError(
            "GIT_HOST_NOT_FOUND",
            "Impossible d'identifier le serveur Git.",
        )


    def extract_repository_path(
        self,
        repository_url: str,
    ) -> str:
        if repository_url.startswith(
            ("https://", "ssh://")
        ):
            path = (
                urlparse(repository_url)
                .path
                .strip("/")
            )

        else:
            match = re.match(
                r"^[^@\s]+@[^:\s]+:(?P<path>.+)$",
                repository_url,
            )

            if not match:
                raise SourceProviderError(
                    "INVALID_REPOSITORY_PATH",
                    "Le chemin du repository est invalide.",
                )

            path = match.group("path")

        if path.endswith(".git"):
            path = path[:-4]

        parts = [
            item
            for item in path.split("/")
            if item
        ]

        if (
            len(parts) < 2
            or ".." in parts
        ):
            raise SourceProviderError(
                "INVALID_REPOSITORY_PATH",
                (
                    "Le repository doit contenir "
                    "un groupe et un projet."
                ),
            )

        return "/".join(parts)


    def timeout_seconds(self) -> int:
        return int(
            current_app.config.get(
                "INTEGRATION_TIMEOUT_SECONDS",
                20,
            )
        )


git_source_provider = GitSourceProvider()