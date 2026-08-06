from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.deployments import repository
from app.deployments.runtime import (
    ArgoCdProvider,
    CommandRunner,
    DeploymentCancelled,
    DeploymentExecutionError,
    DeploymentLogger,
    DeploymentWorkspace,
    DockerProvider,
    GitOpsProvider,
    KubernetesProvider,
    WorkspaceProvider,
)


STAGE_SCOPES = {
    "prepare": "system",
    "source": "system",
    "build": "docker",
    "registry": "nexus",
    "gitops": "gitops",
    "argocd": "argocd",
    "kubernetes": "kubernetes",
    "health": "application",
}


class DeploymentPipeline:
    def __init__(self, deployment: dict[str, Any]) -> None:
        self.deployment = deployment
        self.deployment_id = int(deployment["id"])
        self.logger = DeploymentLogger(self.deployment_id)
        self.runner = CommandRunner(
            deployment_id=self.deployment_id,
            logger=self.logger,
        )
        self.workspace = DeploymentWorkspace.for_deployment(self.deployment_id)

        contract_row = repository.find_confirmed_contract(
            int(deployment["project_id"])
        )
        if contract_row is None:
            raise DeploymentExecutionError(
                "DEPLOYMENT_CONTRACT_MISSING",
                "Le contrat interne confirmé est introuvable.",
                stage="prepare",
                title="Proposition non confirmée",
                retryable=False,
                requires_new_generation=True,
            )
        self.contract = contract_row.get("deployment_contract") or {}

        environment_id = int(deployment["environment_id"])
        self.registry_connection = self._require_connection(
            environment_id,
            "container_registry",
            "Registre Nexus",
            "registry",
        )
        self.gitops_connection = self._require_connection(
            environment_id,
            "gitops_repository",
            "Repository GitOps",
            "gitops",
        )
        self.argocd_connection = self._require_connection(
            environment_id,
            "argocd",
            "Argo CD",
            "argocd",
        )
        self.kubernetes_connection = self._require_connection(
            environment_id,
            "kubernetes",
            "Kubernetes",
            "kubernetes",
        )

        self.workspace_provider = WorkspaceProvider(
            deployment=deployment,
            workspace=self.workspace,
            logger=self.logger,
        )
        self.docker_provider = DockerProvider(
            deployment=deployment,
            workspace=self.workspace,
            logger=self.logger,
            runner=self.runner,
            registry_connection=self.registry_connection,
        )
        self.gitops_provider = GitOpsProvider(
            deployment=deployment,
            workspace=self.workspace,
            logger=self.logger,
            runner=self.runner,
            gitops_connection=self.gitops_connection,
            contract=self.contract,
        )
        self.argocd_provider = ArgoCdProvider(
            deployment=deployment,
            workspace=self.workspace,
            logger=self.logger,
            connection=self.argocd_connection,
        )
        self.kubernetes_provider = KubernetesProvider(
            deployment=deployment,
            logger=self.logger,
            connection=self.kubernetes_connection,
        )

    @staticmethod
    def _require_connection(
        environment_id: int,
        role: str,
        label: str,
        stage: str,
    ) -> dict[str, Any]:
        connection = repository.find_environment_connection(
            environment_id=environment_id,
            service_role=role,
        )
        if connection is None:
            raise DeploymentExecutionError(
                "ENVIRONMENT_SERVICE_MISSING",
                f"L’environnement ne contient pas le service {label}.",
                stage=stage,
                title=f"{label} absent",
                retryable=False,
                integration_name=None,
            )
        if not connection.get("enabled"):
            raise DeploymentExecutionError(
                "ENVIRONMENT_SERVICE_DISABLED",
                f"La connexion {connection['name']} est désactivée.",
                stage=stage,
                title=f"{label} désactivé",
                retryable=True,
                integration_name=connection.get("name"),
            )
        return connection

    def run(self) -> None:
        try:
            self._run_steps()
        except DeploymentCancelled:
            self._mark_cancelled()
        except DeploymentExecutionError as error:
            self._mark_failed(error)
        except Exception as error:
            self._mark_failed(
                DeploymentExecutionError(
                    "UNEXPECTED_DEPLOYMENT_ERROR",
                    str(error),
                    stage=str(self.deployment.get("current_stage") or "prepare"),
                    title="Erreur interne du worker",
                    retryable=True,
                )
            )
        finally:
            repository.release_deployment_lock(self.deployment_id)

    def _run_steps(self) -> None:
        steps = repository.list_deployment_steps(self.deployment_id)
        for step in steps:
            status = str(step.get("status") or "pending")
            if status in {"succeeded", "skipped"}:
                continue
            if status == "cancelled":
                raise DeploymentCancelled("Le déploiement a été annulé.")

            stage = str(step.get("stage") or step.get("code"))

            if (
                stage == "argocd"
                and self.deployment.get("sync_mode") == "confirm_before_sync"
                and not self.deployment.get("sync_confirmed_at")
            ):
                repository.update_deployment(
                    self.deployment_id,
                    status="waiting_confirmation",
                    current_stage="argocd",
                    current_stage_label="Confirmation Argo CD requise",
                    locked_at=None,
                    locked_by=None,
                )
                self.logger.write(
                    "argocd",
                    "warning",
                    "Le pipeline attend votre confirmation avant la synchronisation Argo CD.",
                    stage="argocd",
                )
                return

            self._run_stage(stage)
            self._refresh_progress(stage)

            if stage == "gitops" and self.deployment.get("sync_mode") == "prepare_only":
                repository.skip_steps(
                    self.deployment_id,
                    ("argocd", "kubernetes", "health"),
                )
                repository.update_deployment(
                    self.deployment_id,
                    status="succeeded",
                    current_stage="gitops",
                    current_stage_label="Release préparée dans GitOps",
                    progress=100,
                    finished_at=datetime.now(timezone.utc),
                )
                self.logger.write(
                    "system",
                    "success",
                    "La release a été préparée et publiée dans GitOps sans synchronisation Argo CD.",
                )
                return

        repository.resolve_incidents(self.deployment_id)
        repository.update_deployment(
            self.deployment_id,
            status="succeeded",
            current_stage="health",
            current_stage_label="Application saine",
            progress=100,
            error_code=None,
            error_message=None,
            finished_at=datetime.now(timezone.utc),
        )
        self.logger.write(
            "system",
            "success",
            "Déploiement terminé avec succès.",
        )

    def _run_stage(self, stage: str) -> None:
        label = next(
            (
                item.get("name")
                for item in repository.list_deployment_steps(self.deployment_id)
                if item.get("stage") == stage
            ),
            stage,
        )
        repository.update_deployment(
            self.deployment_id,
            status="running",
            current_stage=stage,
            current_stage_label=str(label),
        )
        repository.update_step(
            deployment_id=self.deployment_id,
            stage=stage,
            status="running",
        )
        self.logger.write(
            STAGE_SCOPES.get(stage, "system"),
            "info",
            f"{label} en cours…",
            stage=stage,
        )

        if stage == "prepare":
            details = self.workspace_provider.prepare()
        elif stage == "source":
            details = self.workspace_provider.checkout_source()
        elif stage == "build":
            details = self.docker_provider.build_images()
        elif stage == "registry":
            details = self.docker_provider.push_images()
        elif stage == "gitops":
            details = self.gitops_provider.publish()
            repository.update_deployment(
                self.deployment_id,
                gitops_commit=details.get("gitopsCommit"),
            )
        elif stage == "argocd":
            details = self.argocd_provider.apply_and_sync()
        elif stage == "kubernetes":
            resources = self.argocd_provider.application_resources()
            resources.extend(self.kubernetes_provider.observe())
            repository.replace_resources(self.deployment_id, resources)
            details = {"resourceCount": len(resources)}
        elif stage == "health":
            resources = self.kubernetes_provider.wait_until_healthy()
            resources.extend(self.argocd_provider.application_resources())
            repository.replace_resources(self.deployment_id, resources)
            argocd_unhealthy = [
                item
                for item in resources
                if item.get("kind") == "argocd_application"
                and item.get("health") == "degraded"
            ]
            if argocd_unhealthy:
                raise DeploymentExecutionError(
                    "ARGOCD_APPLICATION_DEGRADED",
                    "Une application Argo CD est en état Degraded.",
                    stage="health",
                    title="Application Argo CD dégradée",
                    retryable=False,
                    requires_new_generation=True,
                )
            details = {"resourceCount": len(resources), "healthy": True}
        else:
            raise DeploymentExecutionError(
                "UNKNOWN_DEPLOYMENT_STAGE",
                f"Étape de déploiement inconnue : {stage}",
                stage=stage,
            )

        repository.update_step(
            deployment_id=self.deployment_id,
            stage=stage,
            status="succeeded",
            details=details,
        )
        self.logger.write(
            STAGE_SCOPES.get(stage, "system"),
            "success",
            f"{label} terminée.",
            stage=stage,
        )

    def _refresh_progress(self, stage: str) -> None:
        steps = repository.list_deployment_steps(self.deployment_id)
        done = sum(
            1
            for step in steps
            if step.get("status") in {"succeeded", "skipped"}
        )
        progress = round((done / max(1, len(steps))) * 100)
        repository.update_deployment(
            self.deployment_id,
            current_stage=stage,
            progress=progress,
        )

    def _mark_cancelled(self) -> None:
        current_stage = str(self.deployment.get("current_stage") or "prepare")
        repository.update_step(
            deployment_id=self.deployment_id,
            stage=current_stage,
            status="cancelled",
        )
        repository.update_deployment(
            self.deployment_id,
            status="cancelled",
            current_stage_label="Annulé par l’utilisateur",
            finished_at=datetime.now(timezone.utc),
            locked_at=None,
            locked_by=None,
        )
        self.logger.write(
            "system",
            "warning",
            "Le déploiement a été annulé.",
            stage=current_stage,
        )

    def _mark_failed(self, error: DeploymentExecutionError) -> None:
        repository.update_step(
            deployment_id=self.deployment_id,
            stage=error.stage,
            status="failed",
            error_code=error.code,
            error_message=error.message,
        )
        repository.update_deployment(
            self.deployment_id,
            status="failed",
            current_stage=error.stage,
            current_stage_label=error.title,
            error_code=error.code,
            error_message=error.message,
            finished_at=datetime.now(timezone.utc),
            locked_at=None,
            locked_by=None,
        )
        repository.create_incident(
            deployment_id=self.deployment_id,
            stage=error.stage,
            code=error.code,
            title=error.title,
            message=error.message,
            component_name=error.component_name,
            integration_name=error.integration_name,
            retryable=error.retryable,
            requires_new_generation=error.requires_new_generation,
        )
        self.logger.write(
            STAGE_SCOPES.get(error.stage, "system"),
            "error",
            f"{error.code} — {error.message}",
            stage=error.stage,
            component_name=error.component_name,
        )
