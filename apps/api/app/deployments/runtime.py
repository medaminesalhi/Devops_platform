from __future__ import annotations

import base64
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote, urljoin, urlparse, urlunparse

import requests
import yaml
from flask import current_app

from app.analysis.git_workspace import git_workspace_manager
from app.analysis.repository import find_project_source
from app.analysis.source_workspace import source_workspace_manager
from app.deployments import repository
from app.integrations.security import decrypt_credential


class DeploymentExecutionError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: str,
        title: str | None = None,
        retryable: bool = False,
        requires_new_generation: bool = False,
        component_name: str | None = None,
        integration_name: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.title = title or "Échec du déploiement"
        self.retryable = retryable
        self.requires_new_generation = requires_new_generation
        self.component_name = component_name
        self.integration_name = integration_name


class DeploymentCancelled(RuntimeError):
    pass


SENSITIVE_PATTERN = re.compile(
    r"(?i)(password|passwd|token|secret|api[_-]?key|private[_-]?key|authorization)"
    r"\s*[:=]\s*([^\s,;]+)"
)


def sanitize_log(value: str, maximum: int = 10000) -> str:
    cleaned = SENSITIVE_PATTERN.sub(r"\1=[REDACTED]", value)
    cleaned = re.sub(
        r"(?i)bearer\s+[a-z0-9._~+/=-]+",
        "Bearer [REDACTED]",
        cleaned,
    )
    return cleaned[-maximum:]


def safe_path(root: Path, relative_path: str) -> Path:
    normalized = relative_path.replace("\\", "/").lstrip("/")
    target = (root / normalized).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as error:
        raise DeploymentExecutionError(
            "UNSAFE_ARTIFACT_PATH",
            f"Le chemin d’artefact est dangereux : {relative_path}",
            stage="prepare",
            requires_new_generation=True,
        ) from error
    return target


def slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized or "application"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DeploymentLogger:
    def __init__(self, deployment_id: int) -> None:
        self.deployment_id = deployment_id

    def write(
        self,
        scope: str,
        level: str,
        message: str,
        *,
        stage: str | None = None,
        component_name: str | None = None,
    ) -> None:
        repository.add_log(
            deployment_id=self.deployment_id,
            scope=scope,
            level=level,
            stage=stage,
            component_name=component_name,
            message=sanitize_log(message),
        )


class CommandRunner:
    def __init__(
        self,
        *,
        deployment_id: int,
        logger: DeploymentLogger,
    ) -> None:
        self.deployment_id = deployment_id
        self.logger = logger

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
        """Arrête la commande et ses enfants (npm -> vite, docker, git...)."""
        if process.poll() is not None:
            return

        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except (ProcessLookupError, PermissionError):
            try:
                process.terminate()
            except ProcessLookupError:
                return

        try:
            process.wait(timeout=10)
            return
        except subprocess.TimeoutExpired:
            pass

        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except (ProcessLookupError, PermissionError):
            try:
                process.kill()
            except ProcessLookupError:
                pass

        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    def run(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        environment: dict[str, str] | None = None,
        timeout: int | None = None,
        scope: str = "system",
        stage: str | None = None,
        component_name: str | None = None,
        stdin_text: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        timeout_seconds = timeout or int(
            current_app.config.get("DEPLOYMENT_COMMAND_TIMEOUT_SECONDS", 1800)
        )
        process_environment = os.environ.copy()
        if environment:
            process_environment.update(environment)

        try:
            process = subprocess.Popen(
                command,
                cwd=str(cwd) if cwd else None,
                env=process_environment,
                stdin=subprocess.PIPE if stdin_text is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                # Nouveau groupe de processus : un cancel tue aussi npm/vite
                # ou tout autre enfant lancé par la commande principale.
                start_new_session=(os.name != "nt"),
            )
        except FileNotFoundError as error:
            raise DeploymentExecutionError(
                "COMMAND_NOT_INSTALLED",
                f"La commande {command[0]} n’est pas installée sur le worker.",
                stage=stage or "prepare",
                title="Outil requis absent",
                retryable=False,
                requires_new_generation=False,
            ) from error

        if stdin_text is not None and process.stdin is not None:
            process.stdin.write(stdin_text)
            process.stdin.close()

        output_lines: list[str] = []
        started = time.monotonic()
        assert process.stdout is not None

        output_queue: queue.Queue[str | None] = queue.Queue()

        def read_stdout() -> None:
            # readline peut bloquer, mais uniquement dans ce thread. Le thread
            # principal reste libre pour vérifier cancel_requested et timeout.
            try:
                for line in iter(process.stdout.readline, ""):
                    output_queue.put(line)
            finally:
                output_queue.put(None)

        reader = threading.Thread(
            target=read_stdout,
            name=f"deployment-{self.deployment_id}-stdout",
            daemon=True,
        )
        reader.start()
        reader_finished = False

        while True:
            if repository.deployment_cancel_requested(self.deployment_id):
                self._terminate_process_tree(process)
                raise DeploymentCancelled("Annulation demandée par l’utilisateur.")

            if time.monotonic() - started > timeout_seconds:
                self._terminate_process_tree(process)
                raise DeploymentExecutionError(
                    "COMMAND_TIMEOUT",
                    f"La commande {command[0]} a dépassé {timeout_seconds} secondes.",
                    stage=stage or "prepare",
                    title="Délai d’exécution dépassé",
                    retryable=True,
                    component_name=component_name,
                )

            try:
                line = output_queue.get(timeout=0.25)
            except queue.Empty:
                line = "__SAPIXI_NO_OUTPUT__"

            if line is None:
                reader_finished = True
            elif line != "__SAPIXI_NO_OUTPUT__":
                normalized = sanitize_log(line.rstrip())
                if normalized:
                    output_lines.append(normalized)
                    self.logger.write(
                        scope,
                        "info",
                        normalized,
                        stage=stage,
                        component_name=component_name,
                    )

            if process.poll() is not None and reader_finished and output_queue.empty():
                break

        return_code = process.wait()
        output = "\n".join(output_lines)
        result = subprocess.CompletedProcess(
            command,
            return_code,
            stdout=output,
            stderr=None,
        )
        if check and return_code != 0:
            raise DeploymentExecutionError(
                "COMMAND_FAILED",
                output[-4000:] or f"La commande {command[0]} a échoué.",
                stage=stage or "prepare",
                retryable=True,
                component_name=component_name,
            )
        return result


@dataclass
class DeploymentWorkspace:
    root: Path
    source: Path
    generated: Path
    gitops_content: Path
    gitops_repository: Path
    auth: Path

    @classmethod
    def for_deployment(cls, deployment_id: int) -> "DeploymentWorkspace":
        configured_root = Path(
            str(
                current_app.config.get(
                    "DEPLOYMENT_WORKSPACE_ROOT",
                    Path(current_app.root_path).parent / "var" / "deployments",
                )
            )
        )
        root = configured_root / str(deployment_id)
        return cls(
            root=root,
            source=root / "source",
            generated=root / "generated",
            gitops_content=root / "gitops-content",
            gitops_repository=root / "gitops-repository",
            auth=root / "auth",
        )

    def ensure(self) -> None:
        for path in (
            self.root,
            self.generated,
            self.gitops_content,
            self.auth,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def clean_source(self) -> None:
        if self.source.exists():
            shutil.rmtree(self.source)
        self.source.mkdir(parents=True, exist_ok=True)

    def clean_gitops_repository(self) -> None:
        if self.gitops_repository.exists():
            shutil.rmtree(self.gitops_repository)


class WorkspaceProvider:
    def __init__(
        self,
        *,
        deployment: dict[str, Any],
        workspace: DeploymentWorkspace,
        logger: DeploymentLogger,
    ) -> None:
        self.deployment = deployment
        self.workspace = workspace
        self.logger = logger

    def prepare(self) -> dict[str, Any]:
        self.workspace.ensure()
        release = {
            "deploymentId": self.deployment["id"],
            "projectId": self.deployment["project_id"],
            "generationId": self.deployment["generation_run_id"],
            "version": self.deployment["version"],
            "sourceCommit": self.deployment.get("source_commit"),
            "environmentId": self.deployment["environment_id"],
            "createdAt": iso_now(),
        }
        (self.workspace.root / "release.json").write_text(
            json.dumps(release, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.logger.write(
            "system",
            "success",
            f"Workspace préparé : {self.workspace.root}",
            stage="prepare",
        )
        return {"workspace": str(self.workspace.root)}

    def checkout_source(self) -> dict[str, Any]:
        project = find_project_source(int(self.deployment["project_id"]))
        if project is None:
            raise DeploymentExecutionError(
                "PROJECT_SOURCE_NOT_FOUND",
                "La source du projet est introuvable.",
                stage="source",
                requires_new_generation=False,
            )

        self.workspace.clean_source()
        source_type = str(project.get("source_type") or "git")
        expected_commit = str(self.deployment.get("source_commit") or "")

        if source_type == "zip":
            with source_workspace_manager.prepare_zip(project) as prepared:
                shutil.copytree(
                    prepared.source_path,
                    self.workspace.source,
                    dirs_exist_ok=True,
                )
                actual_version = prepared.version
        else:
            with git_workspace_manager.checkout(
                project=project,
                commit_policy="confirmed",
            ) as checkout:
                shutil.copytree(
                    checkout.source_path,
                    self.workspace.source,
                    dirs_exist_ok=True,
                )
                actual_version = checkout.analyzed_commit_sha

        if expected_commit and actual_version != expected_commit:
            raise DeploymentExecutionError(
                "SOURCE_VERSION_MISMATCH",
                (
                    "La version récupérée ne correspond pas au commit approuvé. "
                    f"Attendu : {expected_commit}; obtenu : {actual_version}."
                ),
                stage="source",
                title="Version source différente",
                retryable=False,
                requires_new_generation=True,
            )

        artifacts = repository.list_generation_artifacts(
            int(self.deployment["generation_run_id"])
        )
        if not artifacts:
            raise DeploymentExecutionError(
                "GENERATION_ARTIFACTS_MISSING",
                "Aucun artefact approuvé n’est disponible.",
                stage="source",
                requires_new_generation=True,
            )

        if self.workspace.generated.exists():
            shutil.rmtree(self.workspace.generated)
        if self.workspace.gitops_content.exists():
            shutil.rmtree(self.workspace.gitops_content)
        self.workspace.generated.mkdir(parents=True, exist_ok=True)
        self.workspace.gitops_content.mkdir(parents=True, exist_ok=True)

        written = 0
        for artifact in artifacts:
            relative_path = str(artifact["relative_path"])
            generated_target = safe_path(self.workspace.generated, relative_path)
            generated_target.parent.mkdir(parents=True, exist_ok=True)
            generated_target.write_text(
                str(artifact.get("content") or ""),
                encoding="utf-8",
            )

            artifact_type = str(artifact.get("artifact_type") or "")
            if artifact_type in {"dockerfile", "dockerignore"}:
                source_target = safe_path(self.workspace.source, relative_path)
                source_target.parent.mkdir(parents=True, exist_ok=True)
                source_target.write_text(
                    str(artifact.get("content") or ""),
                    encoding="utf-8",
                )
            else:
                gitops_target = safe_path(self.workspace.gitops_content, relative_path)
                gitops_target.parent.mkdir(parents=True, exist_ok=True)
                gitops_target.write_text(
                    str(artifact.get("content") or ""),
                    encoding="utf-8",
                )
            written += 1

        self.logger.write(
            "system",
            "success",
            f"Source {actual_version[:12]} et {written} artefact(s) chargés.",
            stage="source",
        )
        return {
            "sourceVersion": actual_version,
            "artifactCount": written,
            "sourcePath": str(self.workspace.source),
        }


class DockerProvider:
    def __init__(
        self,
        *,
        deployment: dict[str, Any],
        workspace: DeploymentWorkspace,
        logger: DeploymentLogger,
        runner: CommandRunner,
        registry_connection: dict[str, Any],
        contract: dict[str, Any],
    ) -> None:
        self.deployment = deployment
        self.workspace = workspace
        self.logger = logger
        self.runner = runner
        self.registry_connection = registry_connection
        self.contract = contract

    def check_docker(self) -> None:
        try:
            self.runner.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                scope="docker",
                stage="build",
                timeout=30,
            )
        except DeploymentExecutionError as error:
            raise DeploymentExecutionError(
                "DOCKER_UNAVAILABLE",
                "Docker n’est pas disponible sur le worker de déploiement.",
                stage="build",
                title="Docker indisponible",
                retryable=True,
            ) from error

    def build_images(self) -> dict[str, Any]:
        self.check_docker()
        components = repository.list_deployment_components(int(self.deployment["id"]))
        if not components:
            raise DeploymentExecutionError(
                "DEPLOYMENT_COMPONENTS_MISSING",
                "Aucun composant n’est associé à la release.",
                stage="build",
                requires_new_generation=True,
            )

        built: list[str] = []
        for component in components:
            component_key = str(component["component_key"])
            component_name = str(component["name"])
            root_path = str(component.get("root_path") or ".")
            context_path = safe_path(self.workspace.source, root_path)
            dockerfile_relative = str(
                component.get("dockerfile_path")
                or f"{root_path.rstrip('/')}/Dockerfile"
            )
            dockerfile_path = safe_path(self.workspace.source, dockerfile_relative)

            if not context_path.exists() or not context_path.is_dir():
                raise DeploymentExecutionError(
                    "DOCKER_CONTEXT_NOT_FOUND",
                    f"Le contexte Docker de {component_name} est introuvable : {root_path}",
                    stage="build",
                    title="Contexte Docker introuvable",
                    retryable=False,
                    requires_new_generation=True,
                    component_name=component_name,
                )
            if not dockerfile_path.is_file():
                raise DeploymentExecutionError(
                    "DOCKERFILE_NOT_FOUND",
                    f"Le Dockerfile de {component_name} est introuvable : {dockerfile_relative}",
                    stage="build",
                    title="Dockerfile introuvable",
                    retryable=False,
                    requires_new_generation=True,
                    component_name=component_name,
                )

            image = f"{component['image_repository']}:{component['image_tag']}"
            repository.update_component_status(
                deployment_id=int(self.deployment["id"]),
                component_key=component_key,
                build_status="running",
            )
            self.logger.write(
                "docker",
                "info",
                f"Construction de {image}",
                stage="build",
                component_name=component_name,
            )
            try:
                self.runner.run(
                    [
                        "docker",
                        "build",
                        "--pull",
                        "--file",
                        str(dockerfile_path),
                        "--tag",
                        image,
                        str(context_path),
                    ],
                    cwd=context_path,
                    scope="docker",
                    stage="build",
                    component_name=component_name,
                )
            except DeploymentExecutionError as error:
                repository.update_component_status(
                    deployment_id=int(self.deployment["id"]),
                    component_key=component_key,
                    build_status="failed",
                )
                raise DeploymentExecutionError(
                    "DOCKER_BUILD_FAILED",
                    error.message,
                    stage="build",
                    title=f"Échec du build de {component_name}",
                    retryable=False,
                    requires_new_generation=True,
                    component_name=component_name,
                ) from error

            inspect = self.runner.run(
                ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
                scope="docker",
                stage="build",
                component_name=component_name,
                timeout=60,
            )
            digest = inspect.stdout.strip().splitlines()[-1] if inspect.stdout.strip() else None
            repository.update_component_status(
                deployment_id=int(self.deployment["id"]),
                component_key=component_key,
                build_status="succeeded",
                image_digest=digest,
            )
            built.append(image)
            self.logger.write(
                "docker",
                "success",
                f"Image construite : {image}",
                stage="build",
                component_name=component_name,
            )
        return {"images": built}

    def _registry_host(self) -> str:
        registry_target = ((self.contract.get("target") or {}).get("registry") or {})
        configured_host = str(registry_target.get("host") or "").strip().rstrip("/")
        if configured_host:
            return configured_host
        endpoint_url = str(registry_target.get("endpointUrl") or "").strip()
        source = endpoint_url or str(self.registry_connection.get("base_url") or "")
        parsed = urlparse(source)
        host = parsed.netloc or parsed.path
        return host.strip().rstrip("/")

    def login(self) -> str:
        host = self._registry_host()
        username = str(self.registry_connection.get("username") or "").strip()
        secret = decrypt_credential(
            self.registry_connection.get("secret_ciphertext")
        )
        if not host:
            raise DeploymentExecutionError(
                "REGISTRY_HOST_MISSING",
                "L’adresse du registre Nexus est absente.",
                stage="registry",
                integration_name=self.registry_connection.get("name"),
            )
        if not username or not secret:
            raise DeploymentExecutionError(
                "REGISTRY_CREDENTIAL_MISSING",
                "Le username ou le secret Nexus est absent.",
                stage="registry",
                title="Credential Nexus absent",
                retryable=True,
                integration_name=self.registry_connection.get("name"),
            )
        try:
            self.runner.run(
                [
                    "docker",
                    "login",
                    host,
                    "--username",
                    username,
                    "--password-stdin",
                ],
                stdin_text=secret + "\n",
                scope="nexus",
                stage="registry",
                timeout=60,
            )
        except DeploymentExecutionError as error:
            message = error.message or ""
            if re.search(r"HTTP response to HTTPS client", message, re.I):
                raise DeploymentExecutionError(
                    "REGISTRY_PROTOCOL_MISMATCH",
                    "Le registre Nexus répond en HTTP alors que Docker tente une connexion HTTPS. "
                    "Déclarez ce registry dans insecure-registries ou activez HTTPS côté Nexus.",
                    stage="registry",
                    title="Protocole du registre Nexus incompatible",
                    retryable=True,
                    integration_name=self.registry_connection.get("name"),
                ) from error
            if re.search(r"unauthorized|authentication required|denied|401|403", message, re.I):
                raise DeploymentExecutionError(
                    "REGISTRY_AUTHENTICATION_FAILED",
                    "Nexus a refusé le credential configuré.",
                    stage="registry",
                    title="Échec de l’authentification Nexus",
                    retryable=True,
                    integration_name=self.registry_connection.get("name"),
                ) from error
            raise DeploymentExecutionError(
                "REGISTRY_CONNECTION_FAILED",
                message or "Connexion au registre Nexus impossible.",
                stage="registry",
                title="Connexion au registre Nexus impossible",
                retryable=True,
                integration_name=self.registry_connection.get("name"),
            ) from error
        return host

    def push_images(self) -> dict[str, Any]:
        host = self.login()
        components = repository.list_deployment_components(int(self.deployment["id"]))
        digests: dict[str, str | None] = {}
        try:
            for component in components:
                component_key = str(component["component_key"])
                component_name = str(component["name"])
                image = f"{component['image_repository']}:{component['image_tag']}"
                repository.update_component_status(
                    deployment_id=int(self.deployment["id"]),
                    component_key=component_key,
                    registry_status="running",
                )
                try:
                    self.runner.run(
                        ["docker", "push", image],
                        scope="nexus",
                        stage="registry",
                        component_name=component_name,
                    )
                except DeploymentExecutionError as error:
                    repository.update_component_status(
                        deployment_id=int(self.deployment["id"]),
                        component_key=component_key,
                        registry_status="failed",
                    )
                    code = (
                        "REGISTRY_AUTHENTICATION_FAILED"
                        if re.search(r"unauthorized|authentication required|denied", error.message, re.I)
                        else "REGISTRY_PUSH_FAILED"
                    )
                    raise DeploymentExecutionError(
                        code,
                        error.message,
                        stage="registry",
                        title=f"Échec de la publication de {component_name}",
                        retryable=True,
                        component_name=component_name,
                        integration_name=self.registry_connection.get("name"),
                    ) from error

                inspect = self.runner.run(
                    [
                        "docker",
                        "image",
                        "inspect",
                        image,
                        "--format",
                        "{{join .RepoDigests \"\\n\"}}",
                    ],
                    scope="nexus",
                    stage="registry",
                    component_name=component_name,
                    timeout=60,
                    check=False,
                )
                repo_digest = next(
                    (
                        line.strip()
                        for line in inspect.stdout.splitlines()
                        if "@sha256:" in line
                    ),
                    None,
                )
                repository.update_component_status(
                    deployment_id=int(self.deployment["id"]),
                    component_key=component_key,
                    registry_status="succeeded",
                    image_digest=repo_digest,
                )
                digests[component_key] = repo_digest
                self.logger.write(
                    "nexus",
                    "success",
                    f"Image publiée : {image}",
                    stage="registry",
                    component_name=component_name,
                )
        finally:
            self.runner.run(
                ["docker", "logout", host],
                scope="nexus",
                stage="registry",
                timeout=30,
                check=False,
            )
        return {"digests": digests}


class GitAuthentication:
    def __init__(
        self,
        *,
        connection: dict[str, Any],
        auth_directory: Path,
    ) -> None:
        self.connection = connection
        self.auth_directory = auth_directory
        self.environment = os.environ.copy()
        self.environment["GIT_TERMINAL_PROMPT"] = "0"
        self.environment["GIT_CONFIG_NOSYSTEM"] = "1"
        self.environment["GIT_ASKPASS_REQUIRE"] = "force"

    def prepare(self, repository_url: str) -> tuple[str, dict[str, str]]:
        secret = decrypt_credential(self.connection.get("secret_ciphertext"))
        username = str(self.connection.get("username") or "").strip()
        if not secret:
            return repository_url, self.environment
        if not username:
            username = "oauth2"

        self.auth_directory.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            askpass = self.auth_directory / "git-askpass.cmd"
            askpass.write_text(
                "@echo off\r\n"
                "echo %SAPIXI_GIT_SECRET%\r\n",
                encoding="utf-8",
            )
        else:
            askpass = self.auth_directory / "git-askpass.sh"
            askpass.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$SAPIXI_GIT_SECRET\"\n",
                encoding="utf-8",
            )
            try:
                askpass.chmod(0o700)
            except OSError:
                pass

        parsed = urlparse(repository_url)
        if parsed.scheme not in {"http", "https"}:
            raise DeploymentExecutionError(
                "GITOPS_TRANSPORT_UNSUPPORTED",
                "Le MVP GitOps utilise une URL HTTPS.",
                stage="gitops",
                title="Transport GitOps non supporté",
                retryable=False,
                integration_name=self.connection.get("name"),
            )
        netloc = f"{quote(username, safe='')}@{parsed.hostname or ''}"
        if parsed.port:
            netloc += f":{parsed.port}"
        authenticated_url = urlunparse(
            (
                parsed.scheme,
                netloc,
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )
        self.environment["GIT_ASKPASS"] = str(askpass)
        self.environment["SAPIXI_GIT_SECRET"] = secret
        return authenticated_url, self.environment


def _update_generated_values(
    *,
    workspace: DeploymentWorkspace,
    deployment: dict[str, Any],
) -> None:
    components = repository.list_deployment_components(int(deployment["id"]))
    for file_path in workspace.gitops_content.rglob("values*.yaml"):
        try:
            data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        relative_parts = {
            part.lower()
            for part in file_path.relative_to(workspace.gitops_content).parts
        }
        component = next(
            (
                item
                for item in components
                if str(item["component_key"]).lower() in relative_parts
            ),
            None,
        )
        if component is None and len(components) == 1:
            component = components[0]
        if component is None:
            continue
        image = data.get("image")
        if not isinstance(image, dict):
            image = {}
            data["image"] = image
        image["repository"] = component["image_repository"]
        image["tag"] = component["image_tag"]
        file_path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )


def _replace_helm_version_placeholders(
    workspace: DeploymentWorkspace,
    version: str,
) -> None:
    for path in workspace.gitops_content.rglob("*.y*ml"):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "__SAPIXI_HELM_VERSION__" in content:
            path.write_text(
                content.replace("__SAPIXI_HELM_VERSION__", version),
                encoding="utf-8",
            )


class GitOpsProvider:
    def __init__(
        self,
        *,
        deployment: dict[str, Any],
        workspace: DeploymentWorkspace,
        logger: DeploymentLogger,
        runner: CommandRunner,
        gitops_connection: dict[str, Any],
        contract: dict[str, Any],
    ) -> None:
        self.deployment = deployment
        self.workspace = workspace
        self.logger = logger
        self.runner = runner
        self.gitops_connection = gitops_connection
        self.contract = contract

    def _gitops_target(self) -> tuple[str, str]:
        delivery = ((self.contract.get("target") or {}).get("delivery") or {})
        repository_url = str(delivery.get("repositoryUrl") or "").strip()
        branch = str(delivery.get("targetRevision") or "main").strip() or "main"
        if not repository_url:
            raise DeploymentExecutionError(
                "GITOPS_REPOSITORY_MISSING",
                "L’URL du repository GitOps est absente.",
                stage="gitops",
                integration_name=self.gitops_connection.get("name"),
            )
        return repository_url, branch

    def _configure_webhook_if_requested(self) -> None:
        delivery = ((self.contract.get("target") or {}).get("delivery") or {})
        if str(delivery.get("refreshMode") or "polling") != "webhook":
            return
        project_id = delivery.get("repositoryId")
        if not project_id:
            self.logger.write(
                "gitops",
                "warning",
                "Webhook non configuré : identifiant GitLab absent. Argo CD utilisera son polling.",
                stage="gitops",
            )
            return
        argocd_url = str(
            ((self.contract.get("target") or {}).get("argocd") or {}).get("serverUrl")
            or ""
        ).rstrip("/")
        secret = decrypt_credential(self.gitops_connection.get("secret_ciphertext"))
        if not argocd_url or not secret:
            self.logger.write(
                "gitops",
                "warning",
                "Webhook non configuré : URL Argo CD ou token GitLab absent. Le polling reste actif.",
                stage="gitops",
            )
            return

        base_url = str(self.gitops_connection.get("base_url") or "").rstrip("/")
        webhook_url = f"{argocd_url}/api/webhook"
        headers = {"PRIVATE-TOKEN": secret, "Accept": "application/json"}
        verify = bool(self.gitops_connection.get("verify_ssl", True))
        try:
            hooks = requests.get(
                f"{base_url}/api/v4/projects/{quote(str(project_id), safe='')}/hooks",
                headers=headers,
                timeout=30,
                verify=verify,
            )
            hook_items: list[dict[str, Any]] = []
            if hooks.status_code == 200:
                candidate = hooks.json()
                if isinstance(candidate, list):
                    hook_items = [item for item in candidate if isinstance(item, dict)]
            if any(
                str(item.get("url") or "").rstrip("/") == webhook_url.rstrip("/")
                for item in hook_items
            ):
                return
            response = requests.post(
                f"{base_url}/api/v4/projects/{quote(str(project_id), safe='')}/hooks",
                headers=headers,
                json={
                    "url": webhook_url,
                    "push_events": True,
                    "enable_ssl_verification": True,
                },
                timeout=30,
                verify=verify,
            )
            if response.status_code not in {200, 201}:
                raise RuntimeError(f"HTTP {response.status_code}")
            self.logger.write(
                "gitops",
                "success",
                "Webhook GitLab vers Argo CD configuré.",
                stage="gitops",
            )
        except Exception as error:
            # Un webhook accélère le refresh mais n'est pas requis : Argo CD poll le repo.
            self.logger.write(
                "gitops",
                "warning",
                f"Webhook GitLab non configuré ({error}). Argo CD utilisera son polling.",
                stage="gitops",
            )

    def publish(self) -> dict[str, Any]:
        repository_url, branch = self._gitops_target()
        _update_generated_values(workspace=self.workspace, deployment=self.deployment)
        self.workspace.clean_gitops_repository()

        auth = GitAuthentication(
            connection=self.gitops_connection,
            auth_directory=self.workspace.auth / "gitops",
        )
        authenticated_url, environment = auth.prepare(repository_url)
        verify_ssl = bool(self.gitops_connection.get("verify_ssl", True))
        git_prefix = ["git", "-c", "credential.helper="]
        if not verify_ssl:
            git_prefix.extend(["-c", "http.sslVerify=false"])

        clone_result = self.runner.run(
            [*git_prefix, "clone", "--branch", branch, authenticated_url, str(self.workspace.gitops_repository)],
            environment=environment,
            scope="gitops",
            stage="gitops",
            timeout=300,
            check=False,
        )
        if clone_result.returncode != 0:
            self.workspace.clean_gitops_repository()
            self.runner.run(
                [*git_prefix, "clone", authenticated_url, str(self.workspace.gitops_repository)],
                environment=environment,
                scope="gitops",
                stage="gitops",
                timeout=300,
            )
            checkout = self.runner.run(
                ["git", "checkout", branch],
                cwd=self.workspace.gitops_repository,
                environment=environment,
                scope="gitops",
                stage="gitops",
                check=False,
            )
            if checkout.returncode != 0:
                self.runner.run(
                    ["git", "checkout", "-b", branch],
                    cwd=self.workspace.gitops_repository,
                    environment=environment,
                    scope="gitops",
                    stage="gitops",
                )

        for source in self.workspace.gitops_content.rglob("*"):
            if not source.is_file():
                continue
            relative = source.relative_to(self.workspace.gitops_content)
            target = safe_path(self.workspace.gitops_repository, relative.as_posix())
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

        self.runner.run(
            ["git", "config", "user.name", "SApixi Deployment Worker"],
            cwd=self.workspace.gitops_repository,
            environment=environment,
            scope="gitops",
            stage="gitops",
        )
        self.runner.run(
            ["git", "config", "user.email", "sapixi-worker@local"],
            cwd=self.workspace.gitops_repository,
            environment=environment,
            scope="gitops",
            stage="gitops",
        )
        self.runner.run(
            ["git", "add", "--all"],
            cwd=self.workspace.gitops_repository,
            environment=environment,
            scope="gitops",
            stage="gitops",
        )
        status = self.runner.run(
            ["git", "status", "--porcelain"],
            cwd=self.workspace.gitops_repository,
            environment=environment,
            scope="gitops",
            stage="gitops",
        )
        if status.stdout.strip():
            message = (
                f"deploy({self.deployment['project_slug']}): "
                f"{self.deployment['version']} [deployment #{self.deployment['id']}]"
            )
            self.runner.run(
                ["git", "commit", "-m", message],
                cwd=self.workspace.gitops_repository,
                environment=environment,
                scope="gitops",
                stage="gitops",
            )
            try:
                self.runner.run(
                    [*git_prefix, "push", "origin", branch],
                    cwd=self.workspace.gitops_repository,
                    environment=environment,
                    scope="gitops",
                    stage="gitops",
                    timeout=300,
                )
            except DeploymentExecutionError as error:
                code = (
                    "GITOPS_AUTHENTICATION_FAILED"
                    if re.search(r"authentication|unauthorized|403|401|denied", error.message, re.I)
                    else "GITOPS_PUSH_FAILED"
                )
                raise DeploymentExecutionError(
                    code,
                    error.message,
                    stage="gitops",
                    title="Échec de la publication GitOps",
                    retryable=True,
                    integration_name=self.gitops_connection.get("name"),
                ) from error

        self._configure_webhook_if_requested()
        commit = self.runner.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.workspace.gitops_repository,
            environment=environment,
            scope="gitops",
            stage="gitops",
        ).stdout.strip().splitlines()[-1]
        self.logger.write(
            "gitops",
            "success",
            f"Commit GitOps publié : {commit[:12]}",
            stage="gitops",
        )
        return {"gitopsCommit": commit, "branch": branch, "mode": "git"}


class HelmRepositoryProvider:
    def __init__(
        self,
        *,
        deployment: dict[str, Any],
        workspace: DeploymentWorkspace,
        logger: DeploymentLogger,
        registry_connection: dict[str, Any],
        contract: dict[str, Any],
    ) -> None:
        self.deployment = deployment
        self.workspace = workspace
        self.logger = logger
        self.registry_connection = registry_connection
        self.contract = contract

    def _delivery(self) -> dict[str, Any]:
        return ((self.contract.get("target") or {}).get("delivery") or {})

    def _package_version(self) -> str:
        return f"0.1.{int(self.deployment['id'])}"

    def _package_charts(self, version: str) -> list[Path]:
        packages_dir = self.workspace.root / "helm-packages"
        if packages_dir.exists():
            shutil.rmtree(packages_dir)
        packages_dir.mkdir(parents=True, exist_ok=True)

        packages: list[Path] = []
        for chart_file in self.workspace.gitops_content.rglob("Chart.yaml"):
            chart_root = chart_file.parent
            try:
                chart = yaml.safe_load(chart_file.read_text(encoding="utf-8"))
            except Exception as error:
                raise DeploymentExecutionError(
                    "HELM_CHART_INVALID",
                    f"Chart.yaml invalide : {chart_file}",
                    stage="gitops",
                    title="Chart Helm invalide",
                    requires_new_generation=True,
                ) from error
            if not isinstance(chart, dict):
                continue
            chart_name = slug(str(chart.get("name") or chart_root.name))
            chart["name"] = chart_name
            chart["version"] = version
            chart["appVersion"] = str(self.deployment.get("version") or version)[:64]
            chart_file.write_text(
                yaml.safe_dump(chart, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            package_path = packages_dir / f"{chart_name}-{version}.tgz"
            with tarfile.open(package_path, "w:gz") as archive:
                for source in sorted(chart_root.rglob("*")):
                    if not source.is_file():
                        continue
                    relative = source.relative_to(chart_root)
                    archive.add(source, arcname=(Path(chart_name) / relative).as_posix())
            packages.append(package_path)
        if not packages:
            raise DeploymentExecutionError(
                "HELM_CHART_MISSING",
                "Aucun Chart.yaml n'est disponible pour la publication Nexus Helm.",
                stage="gitops",
                title="Chart Helm absent",
                requires_new_generation=True,
            )
        return packages

    def publish(self) -> dict[str, Any]:
        delivery = self._delivery()
        repository_name = str(delivery.get("repositoryName") or "").strip()
        if not repository_name:
            raise DeploymentExecutionError(
                "HELM_REPOSITORY_MISSING",
                "Le repository Helm Nexus est absent du contrat.",
                stage="gitops",
                title="Repository Helm absent",
                requires_new_generation=True,
            )

        _update_generated_values(workspace=self.workspace, deployment=self.deployment)
        version = self._package_version()
        _replace_helm_version_placeholders(self.workspace, version)
        packages = self._package_charts(version)

        base_url = str(self.registry_connection.get("base_url") or "").rstrip("/")
        username = str(self.registry_connection.get("username") or "").strip()
        secret = decrypt_credential(self.registry_connection.get("secret_ciphertext"))
        auth = (username, secret) if username and secret else None
        verify = bool(self.registry_connection.get("verify_ssl", True))

        for package_path in packages:
            try:
                with package_path.open("rb") as stream:
                    response = requests.post(
                        f"{base_url}/service/rest/v1/components",
                        params={"repository": repository_name},
                        files={
                            "helm.asset": (
                                package_path.name,
                                stream,
                                "application/gzip",
                            )
                        },
                        auth=auth,
                        timeout=120,
                        verify=verify,
                    )
            except requests.RequestException as error:
                raise DeploymentExecutionError(
                    "HELM_REPOSITORY_UNAVAILABLE",
                    f"Nexus Helm est inaccessible : {error}",
                    stage="gitops",
                    title="Nexus Helm inaccessible",
                    retryable=True,
                    integration_name=self.registry_connection.get("name"),
                ) from error
            if response.status_code not in {200, 201, 204}:
                code = (
                    "HELM_REPOSITORY_AUTHENTICATION_FAILED"
                    if response.status_code in {401, 403}
                    else "HELM_UPLOAD_FAILED"
                )
                raise DeploymentExecutionError(
                    code,
                    sanitize_log(response.text or f"HTTP {response.status_code}"),
                    stage="gitops",
                    title="Échec de la publication Helm",
                    retryable=True,
                    integration_name=self.registry_connection.get("name"),
                )
            self.logger.write(
                "gitops",
                "success",
                f"Chart Helm publié : {package_path.name} dans {repository_name}",
                stage="gitops",
            )

        return {
            "mode": "helm",
            "helmVersion": version,
            "repositoryName": repository_name,
            "packageCount": len(packages),
        }


class ArgoCdProvider:
    def __init__(
        self,
        *,
        deployment: dict[str, Any],
        workspace: DeploymentWorkspace,
        logger: DeploymentLogger,
        connection: dict[str, Any],
        source_connection: dict[str, Any],
        contract: dict[str, Any],
        kubernetes_connection: dict[str, Any] | None = None,
    ) -> None:
        self.deployment = deployment
        self.workspace = workspace
        self.logger = logger
        self.connection = connection
        self.source_connection = source_connection
        self.contract = contract
        self.kubernetes_connection = kubernetes_connection or {}
        self.base_url = str(connection.get("base_url") or "").rstrip("/")
        self.verify_ssl = bool(connection.get("verify_ssl", True))
        self.secret = decrypt_credential(connection.get("secret_ciphertext"))
        self.timeout = int(current_app.config.get("DEPLOYMENT_HTTP_TIMEOUT_SECONDS", 30))
        self.destination_server: str | None = None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.secret:
            headers["Authorization"] = f"Bearer {self.secret}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        expected: Iterable[int] = (200, 201),
    ) -> requests.Response:
        if not self.base_url:
            raise DeploymentExecutionError(
                "ARGOCD_URL_MISSING",
                "L’URL Argo CD est absente.",
                stage="argocd",
                integration_name=self.connection.get("name"),
            )
        try:
            response = requests.request(
                method,
                urljoin(self.base_url + "/", path.lstrip("/")),
                headers=self._headers(),
                json=json_body,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
        except requests.RequestException as error:
            raise DeploymentExecutionError(
                "ARGOCD_UNAVAILABLE",
                f"Argo CD est inaccessible : {error}",
                stage="argocd",
                title="Argo CD inaccessible",
                retryable=True,
                integration_name=self.connection.get("name"),
            ) from error
        if response.status_code not in set(expected):
            code = (
                "ARGOCD_AUTHENTICATION_FAILED"
                if response.status_code in {401, 403}
                else "ARGOCD_API_FAILED"
            )
            raise DeploymentExecutionError(
                code,
                sanitize_log(response.text or f"HTTP {response.status_code}"),
                stage="argocd",
                title="Erreur Argo CD",
                retryable=True,
                integration_name=self.connection.get("name"),
            )
        return response

    @staticmethod
    def _normalize_server(value: Any) -> str:
        return str(value or "").strip().rstrip("/")

    def _source_repository_body(self) -> tuple[str, dict[str, Any]]:
        delivery = ((self.contract.get("target") or {}).get("delivery") or {})
        repository_url = str(delivery.get("repositoryUrl") or "").strip()
        if not repository_url:
            raise DeploymentExecutionError(
                "ARGOCD_SOURCE_REPOSITORY_MISSING",
                "La source Argo CD ne contient aucune URL de repository.",
                stage="argocd",
                title="Source Argo CD absente",
                requires_new_generation=True,
            )

        source_secret = decrypt_credential(self.source_connection.get("secret_ciphertext"))
        username = str(self.source_connection.get("username") or "").strip()
        mode = str(delivery.get("mode") or "git")
        body: dict[str, Any] = {
            "repo": repository_url,
            "type": "helm" if mode == "helm" else "git",
            "insecure": not bool(self.source_connection.get("verify_ssl", True)),
        }
        if username:
            body["username"] = username
        elif mode == "git" and source_secret:
            body["username"] = "oauth2"
        if source_secret:
            body["password"] = source_secret
        if mode == "helm":
            body["name"] = str(delivery.get("repositoryName") or "sapixi-helm")
        return repository_url, body

    def _ensure_source_repository(self) -> None:
        """
        Crée ou met à jour la source Argo CD.

        L'ancien code quittait immédiatement si l'URL existait déjà. Cela
        conservait d'anciens identifiants dans Argo CD et pouvait provoquer
        des 401 même après correction des credentials dans SApixi.
        """
        repository_url, body = self._source_repository_body()

        # RepoCreateRequest expose le champ `upsert`; grpc-gateway l'accepte
        # comme query parameter tandis que le corps HTTP reste l'objet repo.
        self._request(
            "POST",
            "/api/v1/repositories?upsert=true",
            json_body=body,
            expected=(200, 201),
        )
        self.logger.write(
            "argocd",
            "success",
            f"Source Argo CD vérifiée/mise à jour : {repository_url}",
            stage="argocd",
        )

    def _list_clusters(self) -> list[dict[str, Any]]:
        response = self._request("GET", "/api/v1/clusters", expected=(200,))
        try:
            payload = response.json()
        except ValueError as error:
            raise DeploymentExecutionError(
                "ARGOCD_CLUSTERS_INVALID_RESPONSE",
                "Argo CD a renvoyé une réponse invalide pour la liste des clusters.",
                stage="argocd",
                title="Liste des clusters Argo CD invalide",
                retryable=True,
                integration_name=self.connection.get("name"),
            ) from error

        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    def _resolve_destination_server(self) -> str:
        """
        Retourne exactement le `server` connu par Argo CD.

        L'adresse utilisée par SApixi pour joindre directement l'API
        Kubernetes peut être différente de celle enregistrée dans Argo CD
        (ex.: https://172.16.0.11:6443 vs https://kubernetes.default.svc).
        """
        clusters = self._list_clusters()
        available = [
            self._normalize_server(item.get("server"))
            for item in clusters
            if self._normalize_server(item.get("server"))
        ]
        available = list(dict.fromkeys(available))

        target = self.contract.get("target") or {}
        argocd = target.get("argocd") or {}
        kubernetes = target.get("kubernetes") or {}

        candidates = [
            self._normalize_server(argocd.get("destinationServer")),
            self._normalize_server(self.kubernetes_connection.get("base_url")),
            self._normalize_server(kubernetes.get("server")),
        ]
        candidates = [item for item in dict.fromkeys(candidates) if item]

        for candidate in candidates:
            if candidate in available:
                self.destination_server = candidate
                return candidate

        # Cas fréquent : Argo CD gère uniquement son cluster local sous
        # kubernetes.default.svc alors que SApixi l'atteint via l'IP du master.
        in_cluster = "https://kubernetes.default.svc"
        if in_cluster in available and len(available) == 1:
            self.destination_server = in_cluster
            self.logger.write(
                "argocd",
                "warning",
                (
                    "La destination Kubernetes configurée n'est pas enregistrée "
                    "telle quelle dans Argo CD. Utilisation du seul cluster "
                    f"disponible : {in_cluster}."
                ),
                stage="argocd",
            )
            return in_cluster

        if len(available) == 1:
            self.destination_server = available[0]
            self.logger.write(
                "argocd",
                "warning",
                (
                    "La destination Kubernetes configurée ne correspond pas "
                    "exactement à Argo CD. Utilisation du seul cluster enregistré : "
                    f"{available[0]}."
                ),
                stage="argocd",
            )
            return available[0]

        configured = candidates[0] if candidates else "non définie"
        known = ", ".join(available) if available else "aucun cluster"
        raise DeploymentExecutionError(
            "ARGOCD_DESTINATION_CLUSTER_NOT_FOUND",
            (
                f"La destination Kubernetes {configured!r} n'est pas enregistrée "
                f"dans Argo CD. Clusters connus : {known}. Configurez "
                "target.argocd.destinationServer ou enregistrez le cluster dans Argo CD."
            ),
            stage="argocd",
            title="Cluster Argo CD introuvable",
            retryable=True,
            integration_name=self.connection.get("name"),
        )

    @staticmethod
    def _rewrite_destination_server(
        projects: list[dict[str, Any]],
        applications: list[dict[str, Any]],
        destination_server: str,
    ) -> int:
        changed = 0
        for project in projects:
            spec = project.get("spec")
            if not isinstance(spec, dict):
                continue
            destinations = spec.get("destinations")
            if not isinstance(destinations, list):
                continue
            for destination in destinations:
                if not isinstance(destination, dict):
                    continue
                if destination.get("server") != destination_server:
                    destination["server"] = destination_server
                    changed += 1

        for application in applications:
            spec = application.get("spec")
            if not isinstance(spec, dict):
                continue
            destination = spec.get("destination")
            if not isinstance(destination, dict):
                destination = {}
                spec["destination"] = destination
            if destination.get("server") != destination_server:
                destination["server"] = destination_server
                changed += 1
        return changed

    def preflight(self) -> dict[str, Any]:
        """Vérifie Argo CD, ses credentials, la source et la destination avant le build."""
        self._request("GET", "/api/version", expected=(200,))
        self._ensure_source_repository()
        destination_server = self._resolve_destination_server()
        self.logger.write(
            "argocd",
            "success",
            f"Préflight Argo CD réussi. Destination : {destination_server}",
            stage="argocd",
        )
        return {"destinationServer": destination_server}

    def _manifests(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        projects: list[dict[str, Any]] = []
        applications: list[dict[str, Any]] = []
        if not self.workspace.gitops_content.exists():
            return projects, applications
        for path in self.workspace.gitops_content.rglob("*.y*ml"):
            try:
                documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
            except Exception:
                continue
            for document in documents:
                if not isinstance(document, dict):
                    continue
                kind = document.get("kind")
                if kind == "AppProject":
                    projects.append(document)
                elif kind == "Application":
                    applications.append(document)
        return projects, applications

    def _generation_contains_application(self) -> bool:
        generation_id = self.deployment.get("generation_run_id")
        if not generation_id:
            return False
        artifacts = repository.list_generation_artifacts(int(generation_id))
        return any(
            str(item.get("artifact_type") or "") == "argocd_application"
            for item in artifacts
        )

    def apply_and_sync(self) -> dict[str, Any]:
        self._ensure_source_repository()
        destination_server = self._resolve_destination_server()
        projects, applications = self._manifests()
        if not applications:
            if self._generation_contains_application():
                raise DeploymentExecutionError(
                    "DEPLOYMENT_WORKSPACE_INCOMPLETE",
                    (
                        "Les manifests Argo CD existent dans la génération, mais "
                        "le workspace local du worker est incomplet. Relancez le "
                        "déploiement : SApixi rechargera la source et les artefacts."
                    ),
                    stage="argocd",
                    title="Workspace de déploiement incomplet",
                    retryable=True,
                    requires_new_generation=False,
                )
            raise DeploymentExecutionError(
                "ARGOCD_APPLICATION_MISSING",
                "Aucun manifeste Application Argo CD n’a été généré.",
                stage="argocd",
                title="Application Argo CD absente",
                retryable=False,
                requires_new_generation=True,
            )

        changed = self._rewrite_destination_server(
            projects,
            applications,
            destination_server,
        )
        if changed:
            self.logger.write(
                "argocd",
                "info",
                (
                    f"Destination Argo CD normalisée vers {destination_server} "
                    f"dans {changed} manifeste(s)."
                ),
                stage="argocd",
            )

        for project in projects:
            name = str((project.get("metadata") or {}).get("name") or "")
            if not name:
                continue
            response = self._request(
                "POST",
                "/api/v1/projects",
                json_body={
                    "project": project,
                    "upsert": True,
                },
                expected=(200, 201, 409),
            )
            if response.status_code == 409:
                self._request(
                    "PUT",
                    f"/api/v1/projects/{quote(name, safe='')}",
                    json_body=project,
                    expected=(200,),
                )
            self.logger.write(
                "argocd",
                "success",
                f"AppProject appliqué : {name}",
                stage="argocd",
            )

        names: list[str] = []
        for application in applications:
            metadata = application.get("metadata") or {}
            name = str(metadata.get("name") or "")
            if not name:
                continue
            self._request(
                "POST",
                "/api/v1/applications?upsert=true",
                json_body=application,
                expected=(200, 201),
            )
            self._request(
                "POST",
                f"/api/v1/applications/{quote(name, safe='')}/sync",
                json_body={"prune": True},
                expected=(200, 201),
            )
            names.append(name)
            self.logger.write(
                "argocd",
                "success",
                f"Synchronisation demandée : {name}",
                stage="argocd",
            )

        self._wait_for_applications(names)

        return {
            "applications": names,
            "destinationServer": destination_server,
        }

    def _wait_for_applications(self, names: list[str]) -> None:
        if not names:
            return

        timeout_seconds = int(
            current_app.config.get("DEPLOYMENT_ARGO_TIMEOUT_SECONDS", 600)
        )
        poll_seconds = max(
            2,
            int(current_app.config.get("DEPLOYMENT_ARGO_POLL_SECONDS", 5)),
        )
        started = time.monotonic()
        last_summary = ""

        while time.monotonic() - started < timeout_seconds:
            pending: list[str] = []

            for name in names:
                response = self._request(
                    "GET",
                    f"/api/v1/applications/{quote(name, safe='')}",
                    expected=(200,),
                )
                body = response.json()
                status = body.get("status") or {}
                sync = (status.get("sync") or {}).get("status") or "Unknown"
                health = (status.get("health") or {}).get("status") or "Unknown"
                operation = status.get("operationState") or {}
                phase = str(operation.get("phase") or "")
                message = str(operation.get("message") or "").strip()

                if phase in {"Failed", "Error"}:
                    raise DeploymentExecutionError(
                        "ARGOCD_SYNC_FAILED",
                        sanitize_log(
                            message
                            or (
                                f"La synchronisation Argo CD de {name} "
                                f"a échoué (sync={sync}, health={health})."
                            )
                        ),
                        stage="argocd",
                        title="Synchronisation Argo CD échouée",
                        retryable=True,
                        integration_name=self.connection.get("name"),
                    )

                if sync == "Synced" and health == "Healthy":
                    continue

                if health == "Degraded" and phase == "Succeeded":
                    raise DeploymentExecutionError(
                        "ARGOCD_APPLICATION_DEGRADED",
                        (
                            f"L'application Argo CD {name} est Degraded après "
                            "la synchronisation. Inspectez les hooks, probes et "
                            "workloads Kubernetes avant de relancer."
                        ),
                        stage="argocd",
                        title="Application Argo CD dégradée",
                        retryable=True,
                        integration_name=self.connection.get("name"),
                    )

                pending.append(
                    f"{name}(sync={sync},health={health},phase={phase or '—'})"
                )

            if not pending:
                self.logger.write(
                    "argocd",
                    "success",
                    "Toutes les applications Argo CD sont Synced et Healthy.",
                    stage="argocd",
                )
                return

            summary = ", ".join(pending)
            if summary != last_summary:
                self.logger.write(
                    "argocd",
                    "info",
                    f"Argo CD progresse encore : {summary}",
                    stage="argocd",
                )
                last_summary = summary

            time.sleep(poll_seconds)

        raise DeploymentExecutionError(
            "ARGOCD_SYNC_TIMEOUT",
            (
                "Les applications Argo CD ne sont pas devenues "
                f"Synced/Healthy avant {timeout_seconds} secondes."
            ),
            stage="argocd",
            title="Délai Argo CD dépassé",
            retryable=True,
            integration_name=self.connection.get("name"),
        )

    def application_resources(self) -> list[dict[str, Any]]:
        _projects, applications = self._manifests()
        resources: list[dict[str, Any]] = []
        for application in applications:
            name = str((application.get("metadata") or {}).get("name") or "")
            if not name:
                continue
            response = self._request(
                "GET",
                f"/api/v1/applications/{quote(name, safe='')}",
                expected=(200,),
            )
            body = response.json()
            status = body.get("status") or {}
            sync = (status.get("sync") or {}).get("status") or "Unknown"
            health = (status.get("health") or {}).get("status") or "Unknown"
            resources.append(
                {
                    "resource_key": f"argocd_application:{name}",
                    "kind": "argocd_application",
                    "name": name,
                    "namespace": "argocd",
                    "status": sync,
                    "health": (
                        "healthy"
                        if health == "Healthy"
                        else "progressing"
                        if health in {"Progressing", "Suspended"}
                        else "degraded"
                        if health in {"Degraded", "Missing"}
                        else "unknown"
                    ),
                    "ready": None,
                    "image": None,
                    "restarts": None,
                    "age": "—",
                    "message": health,
                    "url": None,
                    "raw": {"sync": sync, "health": health},
                }
            )
        return resources


class KubernetesProvider:
    def __init__(
        self,
        *,
        deployment: dict[str, Any],
        logger: DeploymentLogger,
        connection: dict[str, Any],
        contract: dict[str, Any] | None = None,
    ) -> None:
        self.deployment = deployment
        self.logger = logger
        self.connection = connection
        self.contract = contract or {}
        self.base_url = str(connection.get("base_url") or "").strip().rstrip("/")
        self.verify_ssl = bool(connection.get("verify_ssl", True))
        self.secret = (
            decrypt_credential(connection.get("secret_ciphertext"))
            or ""
        ).strip()
        self.username = connection.get("username")
        self.timeout = int(current_app.config.get("DEPLOYMENT_HTTP_TIMEOUT_SECONDS", 30))

        target = self.contract.get("target") if isinstance(self.contract, dict) else {}
        target = target if isinstance(target, dict) else {}
        self.namespace = str(
            target.get("namespace")
            or deployment.get("namespace")
            or "default"
        ).strip()

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.secret:
            headers["Authorization"] = f"Bearer {self.secret}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        expected: Iterable[int] = (200,),
    ) -> requests.Response:
        if not self.base_url:
            raise DeploymentExecutionError(
                "KUBERNETES_URL_MISSING",
                "L’URL de l’API Kubernetes est absente.",
                stage="kubernetes",
                integration_name=self.connection.get("name"),
            )

        headers = self._headers()
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        try:
            response = requests.request(
                method,
                urljoin(self.base_url + "/", path.lstrip("/")),
                headers=headers,
                json=json_body,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
        except requests.RequestException as error:
            raise DeploymentExecutionError(
                "KUBERNETES_UNAVAILABLE",
                f"Kubernetes est inaccessible : {error}",
                stage="kubernetes",
                title="Kubernetes inaccessible",
                retryable=True,
                integration_name=self.connection.get("name"),
            ) from error

        if response.status_code == 401:
            raise DeploymentExecutionError(
                "KUBERNETES_TOKEN_INVALID",
                (
                    "Kubernetes a retourné HTTP 401 : le token est absent, "
                    "invalide ou expiré."
                ),
                stage="kubernetes",
                title="Token Kubernetes invalide",
                retryable=True,
                integration_name=self.connection.get("name"),
            )

        if response.status_code == 403:
            details = sanitize_log(response.text or f"HTTP 403 sur {path}")
            raise DeploymentExecutionError(
                "KUBERNETES_RBAC_FORBIDDEN",
                (
                    "Kubernetes a retourné HTTP 403. Le token est valide mais "
                    f"l'accès est interdit pour le namespace {self.namespace!r}. "
                    f"Détail API : {details}"
                ),
                stage="kubernetes",
                title="Permissions Kubernetes insuffisantes",
                retryable=True,
                integration_name=self.connection.get("name"),
            )

        if response.status_code not in set(expected):
            raise DeploymentExecutionError(
                "KUBERNETES_API_FAILED",
                sanitize_log(response.text or f"HTTP {response.status_code}"),
                stage="kubernetes",
                retryable=True,
                integration_name=self.connection.get("name"),
            )

        return response

    def _get(self, path: str) -> dict[str, Any]:
        response = self._request("GET", path, expected=(200,))
        body = response.json()
        return body if isinstance(body, dict) else {}

    def ensure_namespace(self) -> None:
        namespace = self.namespace or "default"
        encoded_namespace = quote(namespace, safe="")

        response = self._request(
            "GET",
            f"/api/v1/namespaces/{encoded_namespace}",
            expected=(200, 404),
        )
        if response.status_code == 200:
            return

        self._request(
            "POST",
            "/api/v1/namespaces",
            json_body={
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {
                    "name": namespace,
                    "labels": {
                        "sapixi.io/managed": "true",
                    },
                },
            },
            expected=(200, 201, 409),
        )
        self.logger.write(
            "kubernetes",
            "success",
            f"Namespace Kubernetes prêt : {namespace}",
            stage="kubernetes",
        )

    def ensure_image_pull_secret(
        self,
        *,
        registry_connection: dict[str, Any],
    ) -> dict[str, Any] | None:
        target = self.contract.get("target") if isinstance(self.contract, dict) else {}
        target = target if isinstance(target, dict) else {}
        registry_target = target.get("registry")
        registry_target = registry_target if isinstance(registry_target, dict) else {}

        secret_name = str(
            registry_target.get("imagePullSecretName")
            or ""
        ).strip()
        if not secret_name:
            return None

        self.ensure_namespace()

        endpoint = str(
            registry_target.get("host")
            or registry_target.get("endpointUrl")
            or registry_connection.get("base_url")
            or ""
        ).strip()
        parsed = urlparse(endpoint if "://" in endpoint else f"//{endpoint}")
        registry_host = (parsed.netloc or parsed.path).strip().rstrip("/")

        username = str(registry_connection.get("username") or "").strip()
        password = (
            decrypt_credential(registry_connection.get("secret_ciphertext"))
            or ""
        ).strip()

        if not registry_host or not username or not password:
            raise DeploymentExecutionError(
                "REGISTRY_PULL_CREDENTIALS_MISSING",
                (
                    "Impossible de provisionner imagePullSecret : "
                    "host/username/credential Nexus incomplet."
                ),
                stage="argocd",
                title="Credential de pull Nexus incomplet",
                retryable=True,
                integration_name=registry_connection.get("name"),
            )

        auth = base64.b64encode(
            f"{username}:{password}".encode("utf-8")
        ).decode("ascii")
        docker_config = {
            "auths": {
                registry_host: {
                    "username": username,
                    "password": password,
                    "auth": auth,
                }
            }
        }
        docker_config_b64 = base64.b64encode(
            json.dumps(docker_config, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")

        namespace = self.namespace or "default"
        encoded_namespace = quote(namespace, safe="")
        encoded_name = quote(secret_name, safe="")
        current = self._request(
            "GET",
            f"/api/v1/namespaces/{encoded_namespace}/secrets/{encoded_name}",
            expected=(200, 404),
        )

        metadata: dict[str, Any] = {
            "name": secret_name,
            "namespace": namespace,
            "labels": {
                "sapixi.io/managed": "true",
            },
        }
        if current.status_code == 200:
            current_body = current.json()
            resource_version = str(
                ((current_body.get("metadata") or {}).get("resourceVersion"))
                or ""
            ).strip()
            if resource_version:
                metadata["resourceVersion"] = resource_version

        body = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": metadata,
            "type": "kubernetes.io/dockerconfigjson",
            "data": {
                ".dockerconfigjson": docker_config_b64,
            },
        }

        if current.status_code == 200:
            self._request(
                "PUT",
                f"/api/v1/namespaces/{encoded_namespace}/secrets/{encoded_name}",
                json_body=body,
                expected=(200,),
            )
        else:
            self._request(
                "POST",
                f"/api/v1/namespaces/{encoded_namespace}/secrets",
                json_body=body,
                expected=(200, 201),
            )

        self.logger.write(
            "kubernetes",
            "success",
            (
                f"imagePullSecret {secret_name} provisionné dans "
                f"le namespace {namespace}."
            ),
            stage="argocd",
        )
        return {
            "name": secret_name,
            "namespace": namespace,
        }

    def preflight(self) -> dict[str, Any]:
        """Vérifie API + namespace + RBAC avant les étapes coûteuses."""
        payload = self._get("/version")
        version = str(
            payload.get("gitVersion")
            or payload.get("git_version")
            or ""
        ).strip()

        namespace = self.namespace or "default"
        self.ensure_namespace()
        encoded_namespace = quote(namespace, safe="")
        checks = [
            ("pods", f"/api/v1/namespaces/{encoded_namespace}/pods?limit=1"),
            ("services", f"/api/v1/namespaces/{encoded_namespace}/services?limit=1"),
            (
                "deployments",
                f"/apis/apps/v1/namespaces/{encoded_namespace}/deployments?limit=1",
            ),
            ("jobs", f"/apis/batch/v1/namespaces/{encoded_namespace}/jobs?limit=1"),
            (
                "ingresses",
                f"/apis/networking.k8s.io/v1/namespaces/{encoded_namespace}/ingresses?limit=1",
            ),
            (
                "persistentvolumeclaims",
                f"/api/v1/namespaces/{encoded_namespace}/persistentvolumeclaims?limit=1",
            ),
        ]

        checked: list[str] = []
        for resource_name, path in checks:
            self._get(path)
            checked.append(resource_name)

        self.logger.write(
            "kubernetes",
            "success",
            (
                "Préflight Kubernetes réussi : "
                f"version={version or 'inconnue'}, "
                f"namespace={namespace}, RBAC vérifié."
            ),
            stage="kubernetes",
        )
        return {
            "version": version or None,
            "namespace": namespace,
            "rbacChecked": checked,
        }

    @staticmethod
    def _age(timestamp: str | None) -> str:
        if not timestamp:
            return "—"
        try:
            created = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            seconds = max(0, int((datetime.now(timezone.utc) - created).total_seconds()))
        except ValueError:
            return "—"
        if seconds < 60:
            return f"{seconds} s"
        if seconds < 3600:
            return f"{seconds // 60} min"
        if seconds < 86400:
            return f"{seconds // 3600} h"
        return f"{seconds // 86400} j"

    def observe(self) -> list[dict[str, Any]]:
        namespace = self.namespace or "default"
        project_slug = str(self.deployment.get("project_slug") or "")
        encoded_namespace = quote(namespace, safe="")
        endpoints = [
            ("deployment", f"/apis/apps/v1/namespaces/{encoded_namespace}/deployments"),
            ("pod", f"/api/v1/namespaces/{encoded_namespace}/pods"),
            ("service", f"/api/v1/namespaces/{encoded_namespace}/services"),
            ("ingress", f"/apis/networking.k8s.io/v1/namespaces/{encoded_namespace}/ingresses"),
            ("job", f"/apis/batch/v1/namespaces/{encoded_namespace}/jobs"),
            ("pvc", f"/api/v1/namespaces/{encoded_namespace}/persistentvolumeclaims"),
        ]
        resources: list[dict[str, Any]] = []
        for kind, path in endpoints:
            body = self._get(path)
            for item in body.get("items") or []:
                metadata = item.get("metadata") or {}
                name = str(metadata.get("name") or "")
                labels = metadata.get("labels") or {}
                if project_slug and (
                    project_slug not in name
                    and labels.get("sapixi.io/project") != project_slug
                    and labels.get("piximind.io/project") != project_slug
                ):
                    continue
                status = item.get("status") or {}
                spec = item.get("spec") or {}
                ready: str | None = None
                image: str | None = None
                restarts: int | None = None
                health = "unknown"
                status_label = "Unknown"
                message: str | None = None
                url: str | None = None

                if kind == "deployment":
                    desired = int(spec.get("replicas") or 0)
                    available = int(status.get("availableReplicas") or 0)
                    ready = f"{available}/{desired}"
                    status_label = "Available" if desired and available >= desired else "Progressing"
                    health = "healthy" if desired and available >= desired else "progressing"
                    containers = ((spec.get("template") or {}).get("spec") or {}).get("containers") or []
                    image = containers[0].get("image") if containers else None
                elif kind == "pod":
                    phase = str(status.get("phase") or "Unknown")
                    container_statuses = status.get("containerStatuses") or []
                    ready_count = sum(1 for value in container_statuses if value.get("ready"))
                    ready = f"{ready_count}/{len(container_statuses)}"
                    restarts = sum(int(value.get("restartCount") or 0) for value in container_statuses)
                    containers = spec.get("containers") or []
                    image = containers[0].get("image") if containers else None
                    status_label = phase
                    health = (
                        "healthy"
                        if phase == "Running" and ready_count == len(container_statuses) and container_statuses
                        else "degraded"
                        if phase in {"Failed", "Unknown"}
                        else "progressing"
                    )
                    conditions = status.get("conditions") or []
                    bad = next((c for c in conditions if c.get("status") == "False" and c.get("message")), None)
                    message = bad.get("message") if bad else status.get("message")
                elif kind == "service":
                    status_label = "Available"
                    health = "healthy"
                elif kind == "ingress":
                    load_balancer = (status.get("loadBalancer") or {}).get("ingress") or []
                    status_label = "Ready" if load_balancer or spec.get("rules") else "Progressing"
                    health = "healthy" if status_label == "Ready" else "progressing"
                    rules = spec.get("rules") or []
                    if rules and rules[0].get("host"):
                        tls = bool(spec.get("tls"))
                        url = f"{'https' if tls else 'http'}://{rules[0]['host']}"
                elif kind == "job":
                    succeeded = int(status.get("succeeded") or 0)
                    failed = int(status.get("failed") or 0)
                    status_label = "Succeeded" if succeeded else "Failed" if failed else "Running"
                    health = "healthy" if succeeded else "degraded" if failed else "progressing"
                elif kind == "pvc":
                    phase = str(status.get("phase") or "Pending")
                    status_label = phase
                    health = "healthy" if phase == "Bound" else "progressing"

                resources.append(
                    {
                        "resource_key": f"{kind}:{namespace}:{name}",
                        "kind": kind,
                        "name": name,
                        "namespace": namespace,
                        "status": status_label,
                        "health": health,
                        "ready": ready,
                        "image": image,
                        "restarts": restarts,
                        "age": self._age(metadata.get("creationTimestamp")),
                        "message": message,
                        "url": url,
                        "raw": {
                            "uid": metadata.get("uid"),
                            "labels": labels,
                        },
                    }
                )
        return resources

    def wait_until_healthy(self) -> list[dict[str, Any]]:
        timeout_seconds = int(
            current_app.config.get("DEPLOYMENT_HEALTH_TIMEOUT_SECONDS", 600)
        )
        poll_seconds = max(
            2,
            int(current_app.config.get("DEPLOYMENT_HEALTH_POLL_SECONDS", 5)),
        )
        started = time.monotonic()
        latest: list[dict[str, Any]] = []
        while time.monotonic() - started < timeout_seconds:
            if repository.deployment_cancel_requested(int(self.deployment["id"])):
                raise DeploymentCancelled("Annulation demandée par l’utilisateur.")
            latest = self.observe()
            repository.replace_resources(int(self.deployment["id"]), latest)
            workloads = [
                item for item in latest if item["kind"] in {"deployment", "pod", "job"}
            ]
            degraded = [item for item in workloads if item["health"] == "degraded"]
            progressing = [item for item in workloads if item["health"] == "progressing"]
            if degraded:
                names = ", ".join(item["name"] for item in degraded[:5])
                raise DeploymentExecutionError(
                    "APPLICATION_UNHEALTHY",
                    f"Des ressources sont dégradées : {names}",
                    stage="health",
                    title="Application dégradée",
                    retryable=False,
                    requires_new_generation=True,
                )
            if workloads and not progressing:
                return latest
            self.logger.write(
                "kubernetes",
                "info",
                "Les ressources Kubernetes progressent encore…",
                stage="health",
            )
            time.sleep(poll_seconds)

        raise DeploymentExecutionError(
            "HEALTH_TIMEOUT",
            "Les ressources Kubernetes ne sont pas devenues saines avant la fin du délai.",
            stage="health",
            title="Délai de santé dépassé",
            retryable=True,
        )
