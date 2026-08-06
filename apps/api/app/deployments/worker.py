from __future__ import annotations

import os
import socket
import time
from datetime import datetime, timezone
from typing import Any

import click
from flask import Flask, current_app

from app.deployments import repository
from app.deployments.pipeline import DeploymentPipeline
from app.deployments.runtime import DeploymentExecutionError


def _fail_before_pipeline(
    deployment: dict[str, Any],
    error: DeploymentExecutionError,
) -> None:
    """Enregistre une erreur apparue pendant l'initialisation du pipeline."""

    deployment_id = int(deployment["id"])
    repository.update_step(
        deployment_id=deployment_id,
        stage=error.stage,
        status="failed",
        error_code=error.code,
        error_message=error.message,
    )
    repository.update_deployment(
        deployment_id,
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
        deployment_id=deployment_id,
        stage=error.stage,
        code=error.code,
        title=error.title,
        message=error.message,
        component_name=error.component_name,
        integration_name=error.integration_name,
        retryable=error.retryable,
        requires_new_generation=error.requires_new_generation,
    )
    repository.add_log(
        deployment_id=deployment_id,
        scope="system",
        level="error",
        stage=error.stage,
        component_name=error.component_name,
        message=f"{error.code} — {error.message}",
    )
    repository.release_deployment_lock(deployment_id)


def run_claimed_deployment(deployment: dict[str, Any]) -> None:
    deployment_id = int(deployment["id"])
    current_app.logger.info(
        "Le worker démarre le déploiement #%s.",
        deployment_id,
    )

    try:
        pipeline = DeploymentPipeline(deployment)
    except DeploymentExecutionError as error:
        _fail_before_pipeline(deployment, error)
        return
    except Exception as error:
        current_app.logger.exception(
            "Impossible d'initialiser le pipeline du déploiement #%s.",
            deployment_id,
        )
        _fail_before_pipeline(
            deployment,
            DeploymentExecutionError(
                "PIPELINE_INITIALIZATION_FAILED",
                str(error),
                stage="prepare",
                title="Initialisation du pipeline impossible",
                retryable=True,
            ),
        )
        return

    pipeline.run()


def register_deployment_commands(app: Flask) -> None:
    @app.cli.command("deployment-worker")
    @click.option(
        "--poll-seconds",
        type=float,
        default=2.0,
        show_default=True,
        help="Délai entre deux lectures de la file PostgreSQL.",
    )
    def deployment_worker(poll_seconds: float) -> None:
        worker_name = f"{socket.gethostname()}:{os.getpid()}"
        click.echo(
            f"Worker de déploiement démarré ({worker_name})."
        )

        while True:
            try:
                deployment = repository.claim_next_deployment(worker_name)
                if deployment is None:
                    time.sleep(max(0.5, poll_seconds))
                    continue
                run_claimed_deployment(deployment)
            except KeyboardInterrupt:
                click.echo("Worker arrêté.")
                return
            except Exception:
                current_app.logger.exception(
                    "Erreur non gérée dans le worker de déploiement."
                )
                time.sleep(max(1.0, poll_seconds))

    @app.cli.command("deployment-run")
    @click.argument("deployment_id", type=int)
    def deployment_run(deployment_id: int) -> None:
        worker_name = f"manual:{socket.gethostname()}:{os.getpid()}"
        deployment = repository.claim_deployment_by_id(
            deployment_id,
            worker_name,
        )
        if deployment is None:
            raise click.ClickException(
                "Le déploiement est introuvable ou n'est pas dans la file."
            )
        run_claimed_deployment(deployment)
        click.echo(f"Déploiement #{deployment_id} traité.")
