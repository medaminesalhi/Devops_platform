from __future__ import annotations

import os
import socket
import time
from typing import Any

import click
from flask import Flask, current_app

from app.performance import repository
from app.performance.runner import K6CancelledError, K6ExecutionError, K6Runner


def run_claimed_performance(run: dict[str, Any], worker_name: str) -> None:
    run_id = int(run["id"])

    repository.add_log(
        run_id,
        level="info",
        message=f"Worker k6 affecté : {worker_name}.",
    )

    mode = str(run.get("mode") or "basic")
    if mode == "observability":
        observability = run.get("observability") or {}
        repository.add_log(
            run_id,
            level="info",
            message=(
                "Mode observability activé. Les métriques temps réel seront "
                f"envoyées vers Prometheus dans le namespace "
                f"{observability.get('namespace', 'inconnu')}."
            ),
        )

    runner = K6Runner()

    def cancel_requested() -> bool:
        return repository.run_cancel_requested(run_id)

    def heartbeat() -> None:
        repository.heartbeat(run_id, worker_name)

    # On conserve seulement les lignes utiles de stdout dans PostgreSQL.
    # k6 peut être bavard : la limite protège la base et l'interface.
    log_budget = int(current_app.config.get("PERFORMANCE_MAX_RUNTIME_LOG_LINES", 300))
    emitted = 0

    def log_line(message: str) -> None:
        nonlocal emitted
        if emitted >= log_budget:
            return
        emitted += 1
        repository.add_log(run_id, level="info", message=message)

    try:
        result = runner.execute(
            run,
            cancel_requested=cancel_requested,
            heartbeat=heartbeat,
            log_line=log_line,
        )

        repository.replace_run_samples(run_id, result.samples)
        repository.add_log(
            run_id,
            level="info",
            message=(
                f"{len(result.samples)} point(s) temporel(s) agrégé(s) "
                "enregistré(s) pour les graphiques."
            ),
        )

        if result.threshold_failed:
            repository.finish_run(
                run_id,
                status="failed",
                exit_code=result.exit_code,
                metrics=result.metrics,
                threshold_results=result.threshold_results,
                summary=result.summary,
                grafana_dashboard_url=result.grafana_dashboard_url,
                error_code="THRESHOLD_FAILED",
                error_message="Un ou plusieurs thresholds k6 ne sont pas respectés.",
            )
            repository.add_log(
                run_id,
                level="error",
                message="Le quality gate de performance a échoué.",
            )
            return

        if result.exit_code != 0:
            repository.finish_run(
                run_id,
                status="failed",
                exit_code=result.exit_code,
                metrics=result.metrics,
                threshold_results=result.threshold_results,
                summary=result.summary,
                grafana_dashboard_url=result.grafana_dashboard_url,
                error_code="K6_EXECUTION_FAILED",
                error_message=f"k6 s'est terminé avec le code {result.exit_code}.",
            )
            repository.add_log(
                run_id,
                level="error",
                message=f"k6 s'est terminé avec le code {result.exit_code}.",
            )
            return

        repository.finish_run(
            run_id,
            status="passed",
            exit_code=result.exit_code,
            metrics=result.metrics,
            threshold_results=result.threshold_results,
            summary=result.summary,
            grafana_dashboard_url=result.grafana_dashboard_url,
            error_code=None,
            error_message=None,
        )
        repository.add_log(
            run_id,
            level="success",
            message="Tous les seuils de performance sont respectés.",
        )

    except K6CancelledError:
        repository.finish_run(
            run_id,
            status="cancelled",
            exit_code=None,
            metrics=None,
            threshold_results=None,
            summary=None,
            grafana_dashboard_url=None,
            error_code="CANCELLED",
            error_message="Le test a été annulé par l'utilisateur.",
        )
        repository.add_log(
            run_id,
            level="warning",
            message="Le processus k6 a été arrêté proprement après la demande d'annulation.",
        )

    except K6ExecutionError as error:
        repository.finish_run(
            run_id,
            status="failed",
            exit_code=None,
            metrics=None,
            threshold_results=None,
            summary=None,
            grafana_dashboard_url=None,
            error_code=error.code,
            error_message=error.message,
        )
        repository.add_log(
            run_id,
            level="error",
            message=f"{error.code} — {error.message}",
        )

    except Exception as error:
        current_app.logger.exception(
            "Erreur non gérée pendant le run k6 #%s.",
            run_id,
        )
        repository.finish_run(
            run_id,
            status="failed",
            exit_code=None,
            metrics=None,
            threshold_results=None,
            summary=None,
            grafana_dashboard_url=None,
            error_code="PERFORMANCE_WORKER_ERROR",
            error_message=str(error),
        )
        repository.add_log(
            run_id,
            level="error",
            message="Erreur interne du worker k6.",
        )


def register_performance_commands(app: Flask) -> None:
    @app.cli.command("k6-worker")
    @click.option(
        "--poll-seconds",
        type=float,
        default=2.0,
        show_default=True,
        help="Délai entre deux lectures de la file PostgreSQL.",
    )
    def k6_worker(poll_seconds: float) -> None:
        worker_name = f"{socket.gethostname()}:{os.getpid()}"
        click.echo(f"Worker k6 démarré ({worker_name}).")

        stale_seconds = int(
            current_app.config.get("PERFORMANCE_STALE_RUN_SECONDS", 300)
        )
        last_recovery = 0.0

        while True:
            try:
                now = time.monotonic()
                if now - last_recovery >= 60:
                    recovered = repository.fail_stale_runs(stale_seconds)
                    if recovered:
                        current_app.logger.warning(
                            "%s run(s) k6 orphelin(s) marqué(s) en échec.",
                            recovered,
                        )
                    last_recovery = now

                run = repository.claim_next_run(worker_name)
                if run is None:
                    time.sleep(max(0.5, poll_seconds))
                    continue

                run_claimed_performance(run, worker_name)

            except KeyboardInterrupt:
                click.echo("Worker k6 arrêté.")
                return
            except Exception:
                current_app.logger.exception(
                    "Erreur non gérée dans le worker k6."
                )
                time.sleep(max(1.0, poll_seconds))

    @app.cli.command("k6-run")
    @click.argument("run_id", type=int)
    def k6_run(run_id: int) -> None:
        worker_name = f"manual:{socket.gethostname()}:{os.getpid()}"
        run = repository.claim_run_by_id(run_id, worker_name)
        if run is None:
            raise click.ClickException(
                "Le run est introuvable, annulé ou n'est plus dans la file."
            )
        run_claimed_performance(run, worker_name)
        click.echo(f"Run k6 #{run_id} traité.")
