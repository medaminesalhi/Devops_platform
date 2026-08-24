from __future__ import annotations

import os
import re
import subprocess
import tempfile

from contextlib import contextmanager

from dataclasses import dataclass

from pathlib import Path

from typing import Iterator

from urllib.parse import (
    quote,
    urlparse,
    urlunparse,
)

from flask import current_app

from app.integrations.security import (
    decrypt_credential,
)


class GitWorkspaceError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
    ) -> None:
        super().__init__(message)

        self.code = code
        self.message = message


@dataclass
class CheckoutResult:
    source_path: Path

    branch_head_sha: str
    analyzed_commit_sha: str

    branch_changed: bool


class GitWorkspaceManager:
    @contextmanager
    def checkout(
        self,
        *,
        project: dict,
        commit_policy: str,
    ) -> Iterator[CheckoutResult]:
        configured_root = (
            current_app.config.get(
                "ANALYSIS_WORKSPACE_ROOT"
            )
            or None
        )

        if configured_root:
            Path(configured_root).mkdir(
                parents=True,
                exist_ok=True,
            )

        with tempfile.TemporaryDirectory(
            prefix=(
                f"piximind-analysis-"
                f"{project['id']}-"
            ),
            dir=configured_root,
        ) as temporary_directory:
            workspace = Path(
                temporary_directory
            )

            source_path = (
                workspace / "source"
            )

            auth_path = (
                workspace / "auth"
            )

            auth_path.mkdir(
                parents=True,
                exist_ok=True,
            )

            environment = os.environ.copy()

            environment[
                "GIT_TERMINAL_PROMPT"
            ] = "0"

            environment[
                "GIT_CONFIG_NOSYSTEM"
            ] = "1"

            repository_url = (
                self.prepare_authentication(
                    project=project,
                    auth_path=auth_path,
                    environment=environment,
                )
            )

            branch = (
                project["default_branch"]
                or "main"
            )

            branch_head_sha = (
                self.read_branch_head(
                    repository_url=
                        repository_url,

                    branch=
                        branch,

                    environment=
                        environment,

                    verify_ssl=bool(
                        project.get(
                            "verify_ssl",
                            True,
                        )
                    ),
                )
            )

            expected_commit = (
                project.get(
                    "last_source_commit_sha"
                )
                or branch_head_sha
            )

            branch_changed = (
                expected_commit
                != branch_head_sha
            )

            analyzed_commit = (
                branch_head_sha
                if commit_policy == "latest"
                else expected_commit
            )

            self.initialize_repository(
                source_path=source_path,

                repository_url=
                    repository_url,

                environment=
                    environment,
            )

            self.fetch_commit(
                source_path=source_path,

                branch=
                    branch,

                commit_sha=
                    analyzed_commit,

                environment=
                    environment,

                verify_ssl=bool(
                    project.get(
                        "verify_ssl",
                        True,
                    )
                ),
            )

            self.run_git(
                [
                    "git",
                    "checkout",
                    "--detach",
                    analyzed_commit,
                ],
                cwd=source_path,
                environment=environment,
            )

            checked_out_commit = (
                self.run_git(
                    [
                        "git",
                        "rev-parse",
                        "HEAD",
                    ],
                    cwd=source_path,
                    environment=environment,
                )
                .stdout
                .strip()
            )

            if checked_out_commit != analyzed_commit:
                raise GitWorkspaceError(
                    "CHECKOUT_COMMIT_MISMATCH",
                    (
                        "Le commit téléchargé ne correspond "
                        "pas au commit demandé."
                    ),
                )

            yield CheckoutResult(
                source_path=source_path,
                branch_head_sha=branch_head_sha,
                analyzed_commit_sha=
                    checked_out_commit,
                branch_changed=branch_changed,
            )


    def get_branch_head(
        self,
        *,
        project: dict,
    ) -> str:
        """Retourne le HEAD distant de la branche sans cloner le repository.

        La prévalidation de déploiement utilise cette méthode pour comparer le
        commit de la génération approuvée avec le HEAD actuellement publié.
        Les mêmes credentials HTTPS/SSH et la même politique TLS que pour
        l'analyse sont réutilisés.
        """
        if str(project.get("source_type") or "git").lower() != "git":
            raise GitWorkspaceError(
                "GIT_SOURCE_REQUIRED",
                "La vérification du HEAD distant nécessite une source Git.",
            )

        environment = os.environ.copy()
        environment["GIT_TERMINAL_PROMPT"] = "0"
        environment["GIT_CONFIG_NOSYSTEM"] = "1"

        configured_root = current_app.config.get("ANALYSIS_WORKSPACE_ROOT") or None
        if configured_root:
            Path(configured_root).mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(
            prefix=f"piximind-head-{project['id']}-",
            dir=configured_root,
        ) as temporary_directory:
            auth_path = Path(temporary_directory) / "auth"
            auth_path.mkdir(parents=True, exist_ok=True)

            repository_url = self.prepare_authentication(
                project=project,
                auth_path=auth_path,
                environment=environment,
            )

            return self.read_branch_head(
                repository_url=repository_url,
                branch=str(project.get("default_branch") or "main"),
                environment=environment,
                verify_ssl=bool(project.get("verify_ssl", True)),
            )


    def prepare_authentication(
        self,
        *,
        project: dict,
        auth_path: Path,
        environment: dict[str, str],
    ) -> str:
        repository_url = str(
            project["repository_url"]
        )

        visibility = (
            project.get(
                "repository_visibility"
            )
            or "private"
        )

        transport = (
            project.get(
                "source_transport"
            )
            or "https"
        )

        if visibility == "public":
            return repository_url

        credential_source = (
            project.get(
                "source_credential_source"
            )
            or "project"
        )

        encrypted_secret = (
            project.get(
                "integration_secret_ciphertext"
            )
            if credential_source
            == "integration"

            else project.get(
                "project_secret_ciphertext"
            )
        )

        secret = decrypt_credential(
            encrypted_secret
        )

        if not secret:
            raise GitWorkspaceError(
                "SOURCE_CREDENTIAL_MISSING",
                (
                    "Le credential Git du projet "
                    "est introuvable."
                ),
            )

        if transport == "https":
            username = (
                project.get("source_username")
                or project.get(
                    "integration_username"
                )
                or "oauth2"
            )

            askpass_path = (
                self.create_askpass_script(
                    auth_path
                )
            )

            environment[
                "GIT_ASKPASS"
            ] = str(askpass_path)

            environment[
                "GIT_ASKPASS_REQUIRE"
            ] = "force"

            environment[
                "PIXIMIND_GIT_USERNAME"
            ] = str(username)

            environment[
                "PIXIMIND_GIT_SECRET"
            ] = secret

            return self.add_username_to_url(
                repository_url,
                str(username),
            )

        private_key_path = (
            auth_path / "deploy_key"
        )

        known_hosts_path = (
            auth_path / "known_hosts"
        )

        normalized_key = secret

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

        private_key_path.write_text(
            normalized_key.strip() + "\n",
            encoding="utf-8",
        )

        try:
            os.chmod(
                private_key_path,
                0o600,
            )
        except OSError:
            pass

        host = (
            project.get("ssh_host")
            or self.extract_host(
                repository_url
            )
        )

        port = int(
            project.get("ssh_port")
            or 22
        )

        scan_result = self.run_process(
            [
                "ssh-keyscan",
                "-p",
                str(port),
                host,
            ],
            environment=environment,
        )

        if not scan_result.stdout.strip():
            raise GitWorkspaceError(
                "SSH_HOST_KEY_NOT_FOUND",
                (
                    "Impossible de récupérer "
                    "l'empreinte SSH du serveur."
                ),
            )

        known_hosts_path.write_text(
            scan_result.stdout,
            encoding="utf-8",
        )

        environment[
            "GIT_SSH_COMMAND"
        ] = (
            f'ssh -i "{private_key_path}" '
            "-o IdentitiesOnly=yes "
            "-o StrictHostKeyChecking=yes "
            f'-o UserKnownHostsFile="{known_hosts_path}" '
            f"-p {port}"
        )

        return repository_url


    def read_branch_head(
        self,
        *,
        repository_url: str,
        branch: str,
        environment: dict[str, str],
        verify_ssl: bool,
    ) -> str:
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
                repository_url,
                f"refs/heads/{branch}",
            ]
        )

        result = self.run_git(
            command,
            environment=environment,
        )

        output = result.stdout.strip()

        if not output:
            raise GitWorkspaceError(
                "SOURCE_BRANCH_NOT_FOUND",
                (
                    f"La branche « {branch} » "
                    "est introuvable."
                ),
            )

        return (
            output
            .splitlines()[0]
            .split()[0]
            .strip()
        )


    def initialize_repository(
        self,
        *,
        source_path: Path,
        repository_url: str,
        environment: dict[str, str],
    ) -> None:
        source_path.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.run_git(
            [
                "git",
                "init",
            ],
            cwd=source_path,
            environment=environment,
        )

        self.run_git(
            [
                "git",
                "remote",
                "add",
                "origin",
                repository_url,
            ],
            cwd=source_path,
            environment=environment,
        )


    def fetch_commit(
        self,
        *,
        source_path: Path,
        branch: str,
        commit_sha: str,
        environment: dict[str, str],
        verify_ssl: bool,
    ) -> None:
        base_command = [
            "git",
            "-c",
            "credential.helper=",
        ]

        if not verify_ssl:
            base_command.extend(
                [
                    "-c",
                    "http.sslVerify=false",
                ]
            )

        branch_fetch = [
            *base_command,
            "fetch",
            "--depth",
            "50",
            "origin",
            f"refs/heads/{branch}",
        ]

        self.run_git(
            branch_fetch,
            cwd=source_path,
            environment=environment,
        )

        commit_exists = subprocess.run(
            [
                "git",
                "cat-file",
                "-e",
                f"{commit_sha}^{{commit}}",
            ],
            cwd=source_path,
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )

        if commit_exists.returncode == 0:
            return

        exact_fetch = [
            *base_command,
            "fetch",
            "--depth",
            "1",
            "origin",
            commit_sha,
        ]

        try:
            self.run_git(
                exact_fetch,
                cwd=source_path,
                environment=environment,
            )

        except GitWorkspaceError as error:
            raise GitWorkspaceError(
                "VALIDATED_COMMIT_UNAVAILABLE",
                (
                    "Le commit validé pendant la phase 1 "
                    "n'est plus récupérable. Lancez l'analyse "
                    "avec la politique « dernier commit »."
                ),
            ) from error


    def run_git(
        self,
        command: list[str],
        *,
        environment: dict[str, str],
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                env=environment,
                timeout=self.timeout_seconds(),
                check=False,
            )

        except FileNotFoundError as error:
            raise GitWorkspaceError(
                "GIT_NOT_INSTALLED",
                (
                    "Git ou OpenSSH n'est pas installé "
                    "sur la machine du backend."
                ),
            ) from error

        except subprocess.TimeoutExpired as error:
            raise GitWorkspaceError(
                "GIT_TIMEOUT",
                (
                    "L'opération Git a dépassé "
                    "le délai autorisé."
                ),
            ) from error

        if result.returncode != 0:
            self.raise_git_error(
                result.stderr
                or result.stdout
                or ""
            )

        return result


    def run_process(
        self,
        command: list[str],
        *,
        environment: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        return self.run_git(
            command,
            environment=environment,
        )


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
            raise GitWorkspaceError(
                "GIT_AUTHENTICATION_FAILED",
                (
                    "GitLab a refusé l'authentification. "
                    "Vérifiez le username, le token, "
                    "le mot de passe ou la clé SSH."
                ),
            )

        if (
            "repository not found" in error
            or "not found" in error
        ):
            raise GitWorkspaceError(
                "GIT_REPOSITORY_NOT_FOUND",
                (
                    "Le repository est introuvable "
                    "ou le credential n'y a pas accès."
                ),
            )

        if (
            "could not resolve host" in error
            or "name or service not known" in error
        ):
            raise GitWorkspaceError(
                "GIT_DNS_ERROR",
                (
                    "Le serveur GitLab ne peut "
                    "pas être résolu."
                ),
            )

        if (
            "certificate" in error
            and "ssl" in error
        ):
            raise GitWorkspaceError(
                "GIT_SSL_ERROR",
                (
                    "Le certificat SSL du serveur "
                    "GitLab n'est pas reconnu."
                ),
            )

        raise GitWorkspaceError(
            "GIT_OPERATION_FAILED",
            (
                "L'opération Git a échoué. "
                "Vérifiez la source et le credential."
            ),
        )


    def create_askpass_script(
        self,
        directory: Path,
    ) -> Path:
        if os.name == "nt":
            path = (
                directory / "git-askpass.bat"
            )

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

        path = (
            directory / "git-askpass.sh"
        )

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

        os.chmod(
            path,
            0o700,
        )

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
            (
                "https://",
                "ssh://",
            )
        ):
            host = urlparse(
                repository_url
            ).hostname

            if host:
                return host

        match = re.match(
            (
                r"^[^@\s]+@"
                r"(?P<host>[^:\s]+):.+$"
            ),
            repository_url,
        )

        if match:
            return match.group("host")

        raise GitWorkspaceError(
            "GIT_HOST_NOT_FOUND",
            (
                "Impossible d'identifier "
                "le serveur GitLab."
            ),
        )


    def timeout_seconds(self) -> int:
        return int(
            current_app.config.get(
                "ANALYSIS_GIT_TIMEOUT_SECONDS",
                120,
            )
        )


git_workspace_manager = (
    GitWorkspaceManager()
)