from __future__ import annotations

import os
import socket
import time
from typing import Any

import click
from flask import Flask, current_app

from app.performance import observability_repository
from app.performance.observability_provisioner import (
    ObservabilityProvisionError,
    provision_observability_stack,
)


def run_claimed_observability(stack: dict[str, Any], worker_name: str) -> None:
    stack_id = int(stack["id"])
    observability_repository.add_log(
        stack_id,
        level="info",
        message=f"Worker observability affecté : {worker_name}.",
    )

    emitted = 0
    log_budget = 500

    def heartbeat() -> None:
        observability_repository.heartbeat(stack_id, worker_name)

    def log_line(message: str) -> None:
        nonlocal emitted
        if emitted >= log_budget:
            return
        emitted += 1
        observability_repository.add_log(
            stack_id,
            level="info",
            message=message,
        )

    try:
        result = provision_observability_stack(
            stack,
            heartbeat=heartbeat,
            log_line=log_line,
        )
        observability_repository.finish_ready(
            stack_id,
            prometheus_remote_write_url=result.prometheus_remote_write_url,
            prometheus_query_url=result.prometheus_query_url,
            grafana_base_url=result.grafana_base_url,
        )
        observability_repository.add_log(
            stack_id,
            level="success",
            message="Prometheus et Grafana sont prêts pour les tests k6.",
        )

    except ObservabilityProvisionError as error:
        observability_repository.finish_failed(
            stack_id,
            code=error.code,
            message=error.message,
        )
        observability_repository.add_log(
            stack_id,
            level="error",
            message=f"{error.code} — {error.message}",
        )

    except Exception as error:
        current_app.logger.exception(
            "Erreur non gérée pendant le provisioning observability #%s.",
            stack_id,
        )
        observability_repository.finish_failed(
            stack_id,
            code="OBSERVABILITY_WORKER_ERROR",
            message=str(error),
        )
        observability_repository.add_log(
            stack_id,
            level="error",
            message="Erreur interne du worker observability.",
        )


def register_observability_commands(app: Flask) -> None:
    @app.cli.command("observability-worker")
    @click.option(
        "--poll-seconds",
        type=float,
        default=3.0,
        show_default=True,
        help="Délai entre deux lectures de la file PostgreSQL.",
    )
    def observability_worker(poll_seconds: float) -> None:
        worker_name = f"{socket.gethostname()}:{os.getpid()}"
        click.echo(f"Worker observability démarré ({worker_name}).")
        last_recovery = 0.0

        while True:
            try:
                now = time.monotonic()
                if now - last_recovery >= 60:
                    recovered = observability_repository.fail_stale_stacks(900)
                    if recovered:
                        current_app.logger.warning(
                            "%s provisioning(s) observability orphelin(s) marqué(s) en échec.",
                            recovered,
                        )
                    last_recovery = now

                stack = observability_repository.claim_next_stack(worker_name)
                if stack is None:
                    time.sleep(max(1.0, poll_seconds))
                    continue

                run_claimed_observability(stack, worker_name)

            except KeyboardInterrupt:
                click.echo("Worker observability arrêté.")
                return
            except Exception:
                current_app.logger.exception(
                    "Erreur non gérée dans le worker observability."
                )
                time.sleep(max(1.0, poll_seconds))
