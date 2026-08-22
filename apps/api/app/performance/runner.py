from __future__ import annotations

import json
import math
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from flask import current_app


@dataclass
class K6RunResult:
    exit_code: int
    elapsed_seconds: float
    metrics: dict[str, Any]
    samples: list[dict[str, Any]]
    threshold_results: list[dict[str, Any]]
    summary: dict[str, Any]
    grafana_dashboard_url: str | None
    threshold_failed: bool


class K6ExecutionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class K6CancelledError(K6ExecutionError):
    def __init__(self) -> None:
        super().__init__("CANCELLED", "Le test k6 a été annulé.")


def _seconds(value: int) -> str:
    return f"{max(1, int(value))}s"


def _split_duration(total: int, ratios: tuple[float, ...]) -> list[int]:
    total = max(len(ratios), int(total))
    values = [max(1, int(round(total * ratio))) for ratio in ratios]
    delta = total - sum(values)
    values[-1] = max(1, values[-1] + delta)
    return values


def _build_scenario(test_type: str, load: dict[str, Any]) -> dict[str, Any]:
    initial = int(load["virtualUsers"])
    maximum = int(load["maxVirtualUsers"])
    duration = int(load["durationSeconds"])

    if test_type == "smoke":
        return {
            "executor": "constant-vus",
            "vus": maximum,
            "duration": _seconds(duration),
            "gracefulStop": "5s",
        }

    if test_type == "spike":
        parts = _split_duration(duration, (0.15, 0.10, 0.50, 0.10, 0.15))
        return {
            "executor": "ramping-vus",
            "startVUs": initial,
            "stages": [
                {"duration": _seconds(parts[0]), "target": initial},
                {"duration": _seconds(parts[1]), "target": maximum},
                {"duration": _seconds(parts[2]), "target": maximum},
                {"duration": _seconds(parts[3]), "target": initial},
                {"duration": _seconds(parts[4]), "target": 0},
            ],
            "gracefulRampDown": "10s",
        }

    if test_type == "soak":
        parts = _split_duration(duration, (0.10, 0.80, 0.10))
    elif test_type == "stress":
        parts = _split_duration(duration, (0.30, 0.50, 0.20))
    else:
        parts = _split_duration(duration, (0.20, 0.60, 0.20))

    return {
        "executor": "ramping-vus",
        "startVUs": initial,
        "stages": [
            {"duration": _seconds(parts[0]), "target": maximum},
            {"duration": _seconds(parts[1]), "target": maximum},
            {"duration": _seconds(parts[2]), "target": 0},
        ],
        "gracefulRampDown": "10s",
    }


def _build_script(run: dict[str, Any]) -> str:
    load = run.get("load_profile") or {}
    thresholds = run.get("thresholds") or {}

    error_rate = float(thresholds.get("errorRatePercent", 1.0)) / 100.0
    checks_rate = float(thresholds.get("checksRatePercent", 99.0)) / 100.0

    options = {
        "scenarios": {
            "performance": _build_scenario(str(run.get("test_type") or "load"), load),
        },
        "thresholds": {
            "http_req_failed": [f"rate<{error_rate:.8f}"],
            "http_req_duration": [
                f"p(95)<{int(thresholds.get('p95Ms', 500))}",
                f"p(99)<{int(thresholds.get('p99Ms', 1000))}",
            ],
            "checks": [f"rate>{checks_rate:.8f}"],
        },
        "summaryTrendStats": [
            "avg",
            "min",
            "med",
            "max",
            "p(90)",
            "p(95)",
            "p(99)",
            "count",
        ],
        "tags": {
            "testid": str(run["id"]),
            "project_id": str(run["project_id"]),
            "test_id": str(run["test_id"]),
            "deployment_id": str(run.get("deployment_id") or "none"),
            "performance_mode": str(run.get("mode") or "basic"),
        },
    }

    target_url = json.dumps(str(run["target_url"]), ensure_ascii=False)
    options_json = json.dumps(options, ensure_ascii=False, indent=2)

    return f"""import http from 'k6/http';
import {{ check }} from 'k6';

export const options = {options_json};

const TARGET_URL = {target_url};

export default function () {{
  const response = http.get(TARGET_URL, {{
    redirects: 5,
    tags: {{ endpoint: 'target' }},
  }});

  check(response, {{
    'HTTP status < 400': (r) => r.status >= 200 && r.status < 400,
  }});
}}
"""


def _number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0

    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]

    rank = (len(ordered) - 1) * percentile
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    if lower == upper:
        return ordered[lower]

    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_k6_points(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise K6ExecutionError(
            "K6_METRICS_MISSING",
            "k6 n'a pas produit le fichier de métriques JSON.",
        )

    points: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            for raw_line in stream:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict) or item.get("type") != "Point":
                    continue
                metric = item.get("metric")
                data = item.get("data")
                if not isinstance(metric, str) or not isinstance(data, dict):
                    continue
                timestamp = _parse_timestamp(data.get("time"))
                if timestamp is None:
                    continue
                points.append(
                    {
                        "metric": metric,
                        "time": timestamp,
                        "value": _number(data.get("value")),
                    }
                )
    except OSError as error:
        raise K6ExecutionError(
            "K6_METRICS_INVALID",
            "Impossible de lire le fichier de métriques produit par k6.",
        ) from error

    return points


def _aggregate_points(
    points: list[dict[str, Any]],
    *,
    configured_duration_seconds: int,
    sample_interval_seconds: int = 2,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    relevant = [
        point
        for point in points
        if point["metric"]
        in {
            "http_reqs",
            "http_req_duration",
            "http_req_failed",
            "checks",
            "iterations",
            "data_received",
            "data_sent",
            "vus",
        }
    ]

    if not relevant:
        raise K6ExecutionError(
            "K6_NO_METRICS",
            "k6 s'est terminé sans produire de métriques exploitables.",
        )

    first_time = min(point["time"] for point in relevant)
    last_time = max(point["time"] for point in relevant)
    observed_span = max(0.001, (last_time - first_time).total_seconds())

    grouped_values: dict[str, list[float]] = {}
    for point in relevant:
        grouped_values.setdefault(point["metric"], []).append(float(point["value"]))

    durations = grouped_values.get("http_req_duration", [])
    failed_values = grouped_values.get("http_req_failed", [])
    checks_values = grouped_values.get("checks", [])

    request_count = int(round(sum(grouped_values.get("http_reqs", []))))
    iteration_count = int(round(sum(grouped_values.get("iterations", []))))
    received_bytes = int(round(sum(grouped_values.get("data_received", []))))
    sent_bytes = int(round(sum(grouped_values.get("data_sent", []))))

    effective_duration = max(
        0.001,
        min(
            float(max(1, configured_duration_seconds)),
            observed_span + float(sample_interval_seconds),
        ),
    )

    metrics = {
        "requests": request_count,
        "rps": round(request_count / effective_duration, 3),
        "avgMs": round(sum(durations) / len(durations), 3) if durations else 0.0,
        "minMs": round(min(durations), 3) if durations else 0.0,
        "maxMs": round(max(durations), 3) if durations else 0.0,
        "p90Ms": round(_percentile(durations, 0.90), 3),
        "p95Ms": round(_percentile(durations, 0.95), 3),
        "p99Ms": round(_percentile(durations, 0.99), 3),
        "errorRatePercent": round(
            (sum(failed_values) / len(failed_values) * 100.0) if failed_values else 0.0,
            4,
        ),
        "checksRatePercent": round(
            (sum(checks_values) / len(checks_values) * 100.0) if checks_values else 0.0,
            4,
        ),
        "dataReceivedBytes": received_bytes,
        "dataSentBytes": sent_bytes,
        "iterations": iteration_count,
    }

    interval = max(1, int(sample_interval_seconds))
    buckets: dict[int, dict[str, Any]] = {}
    for point in relevant:
        elapsed = max(0.0, (point["time"] - first_time).total_seconds())
        bucket_index = int(elapsed // interval)
        bucket = buckets.setdefault(
            bucket_index,
            {
                "sampledAt": point["time"],
                "http_reqs": [],
                "http_req_duration": [],
                "http_req_failed": [],
                "checks": [],
                "iterations": [],
                "vus": [],
            },
        )
        if point["time"] > bucket["sampledAt"]:
            bucket["sampledAt"] = point["time"]
        metric = point["metric"]
        if metric in bucket:
            bucket[metric].append(float(point["value"]))

    samples: list[dict[str, Any]] = []
    cumulative_requests = 0
    cumulative_iterations = 0
    last_vus = 0

    for bucket_index in sorted(buckets):
        bucket = buckets[bucket_index]
        requests_in_bucket = int(round(sum(bucket["http_reqs"])))
        iterations_in_bucket = int(round(sum(bucket["iterations"])))
        cumulative_requests += requests_in_bucket
        cumulative_iterations += iterations_in_bucket

        if bucket["vus"]:
            last_vus = max(0, int(round(bucket["vus"][-1])))

        latency_values = bucket["http_req_duration"]
        failed_bucket = bucket["http_req_failed"]
        checks_bucket = bucket["checks"]

        samples.append(
            {
                "sampledAt": bucket["sampledAt"],
                "elapsedSeconds": bucket_index * interval,
                "vus": last_vus,
                "requests": requests_in_bucket,
                "requestsTotal": cumulative_requests,
                "iterationsTotal": cumulative_iterations,
                "rps": round(requests_in_bucket / interval, 3),
                "avgMs": round(
                    sum(latency_values) / len(latency_values), 3
                ) if latency_values else 0.0,
                "p95Ms": round(_percentile(latency_values, 0.95), 3),
                "p99Ms": round(_percentile(latency_values, 0.99), 3),
                "errorRatePercent": round(
                    (sum(failed_bucket) / len(failed_bucket) * 100.0)
                    if failed_bucket
                    else 0.0,
                    4,
                ),
                "checksRatePercent": round(
                    (sum(checks_bucket) / len(checks_bucket) * 100.0)
                    if checks_bucket
                    else 0.0,
                    4,
                ),
            }
        )

    summary = {
        "source": "k6-json-output",
        "observedSpanSeconds": round(observed_span, 3),
        "configuredDurationSeconds": int(configured_duration_seconds),
        "pointCount": len(relevant),
        "sampleIntervalSeconds": interval,
        "sampleCount": len(samples),
        "metrics": metrics,
    }
    return metrics, samples, summary


def _threshold_results(
    metrics: dict[str, Any],
    thresholds: dict[str, Any],
) -> list[dict[str, Any]]:
    expected_error = float(thresholds.get("errorRatePercent", 1.0))
    expected_p95 = float(thresholds.get("p95Ms", 500))
    expected_p99 = float(thresholds.get("p99Ms", 1000))
    expected_checks = float(thresholds.get("checksRatePercent", 99.0))

    actual_error = float(metrics.get("errorRatePercent") or 0.0)
    actual_p95 = float(metrics.get("p95Ms") or 0.0)
    actual_p99 = float(metrics.get("p99Ms") or 0.0)
    actual_checks = float(metrics.get("checksRatePercent") or 0.0)

    return [
        {
            "key": "error_rate",
            "label": "Taux d’erreur",
            "expected": f"< {expected_error:g} %",
            "actual": f"{actual_error:.2f} %",
            "passed": actual_error < expected_error,
        },
        {
            "key": "p95",
            "label": "Latence p95",
            "expected": f"< {expected_p95:g} ms",
            "actual": f"{actual_p95:.0f} ms",
            "passed": actual_p95 < expected_p95,
        },
        {
            "key": "p99",
            "label": "Latence p99",
            "expected": f"< {expected_p99:g} ms",
            "actual": f"{actual_p99:.0f} ms",
            "passed": actual_p99 < expected_p99,
        },
        {
            "key": "checks",
            "label": "Checks réussis",
            "expected": f"> {expected_checks:g} %",
            "actual": f"{actual_checks:.2f} %",
            "passed": actual_checks > expected_checks,
        },
    ]


def prometheus_remote_write_url(run: dict[str, Any]) -> str | None:
    if run.get("mode") != "observability":
        return None

    observability = run.get("observability") or {}
    url = str(observability.get("prometheusRemoteWriteUrl") or "").strip()
    if not url:
        raise K6ExecutionError(
            "PROMETHEUS_REMOTE_WRITE_URL_MISSING",
            "L'URL Prometheus Remote Write est absente du run.",
        )
    return url


def grafana_dashboard_url(run: dict[str, Any]) -> str | None:
    if run.get("mode") != "observability":
        return None

    observability = run.get("observability") or {}
    base_url = str(observability.get("grafanaBaseUrl") or "").strip().rstrip("/")
    if not base_url:
        return None

    dashboard_uid = str(
        observability.get("grafanaDashboardUid") or "k6-performance"
    ).strip() or "k6-performance"

    return (
        f"{base_url}/d/{quote(dashboard_uid, safe='')}"
        f"?var-testid={quote(str(run['id']), safe='')}"
        f"&var-project={quote(str(run['project_id']), safe='')}"
        f"&var-deployment={quote(str(run.get('deployment_id') or 'none'), safe='')}"
    )


class K6Runner:
    def __init__(self) -> None:
        self.binary = str(current_app.config.get("PERFORMANCE_K6_BINARY", "k6"))
        self.workspace_root = Path(
            str(current_app.config["PERFORMANCE_WORKSPACE_ROOT"])
        ).resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def execute(
        self,
        run: dict[str, Any],
        *,
        cancel_requested: Callable[[], bool],
        heartbeat: Callable[[], None],
        log_line: Callable[[str], None],
    ) -> K6RunResult:
        binary_path = shutil.which(self.binary)
        if binary_path is None:
            raise K6ExecutionError(
                "K6_UNAVAILABLE",
                f"Le binaire k6 '{self.binary}' est introuvable dans le worker.",
            )

        run_id = int(run["id"])
        workspace = self.workspace_root / str(run_id)
        workspace.mkdir(parents=True, exist_ok=True)

        script_path = workspace / "script.js"
        raw_metrics_path = workspace / "metrics.json"
        script_path.write_text(_build_script(run), encoding="utf-8")

        try:
            raw_metrics_path.unlink(missing_ok=True)
        except OSError:
            pass

        # On utilise l'output JSON stable de k6 comme source de vérité.
        # Contrairement à --summary-export, son format Point/Metric conserve
        # chaque mesure avec timestamp et permet aussi de construire les courbes.
        command = [
            binary_path,
            "run",
            "--summary-mode=compact",
            "--out",
            "json=metrics.json",
        ]

        remote_write_url = prometheus_remote_write_url(run)
        environment = os.environ.copy()
        environment["K6_NO_USAGE_REPORT"] = "true"

        if remote_write_url:
            command.extend(["--out", "experimental-prometheus-rw"])
            environment["K6_PROMETHEUS_RW_SERVER_URL"] = remote_write_url
            environment["K6_PROMETHEUS_RW_TREND_STATS"] = (
                "p(90),p(95),p(99),min,max,avg"
            )

        command.append(str(script_path))

        configured_duration = int(
            (run.get("load_profile") or {}).get("durationSeconds") or 1
        )
        timeout_seconds = configured_duration + int(
            current_app.config.get("PERFORMANCE_RUN_GRACE_SECONDS", 120)
        )

        output_queue: queue.Queue[str | None] = queue.Queue()
        popen_kwargs: dict[str, Any] = {
            "cwd": str(workspace),
            "env": environment,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
            "shell": False,
        }

        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        started = time.monotonic()
        process: subprocess.Popen[str] = subprocess.Popen(command, **popen_kwargs)

        def read_output() -> None:
            try:
                if process.stdout is None:
                    return
                for line in process.stdout:
                    output_queue.put(line)
            finally:
                output_queue.put(None)

        output_thread = threading.Thread(
            target=read_output,
            name=f"k6-output-{run_id}",
            daemon=True,
        )
        output_thread.start()

        def flush_output(wait_seconds: float = 0.0) -> None:
            first = True
            while True:
                try:
                    if first and wait_seconds > 0:
                        line = output_queue.get(timeout=wait_seconds)
                    else:
                        line = output_queue.get_nowait()
                except queue.Empty:
                    return

                first = False
                if line is None:
                    return

                cleaned = line.strip()
                if cleaned:
                    log_line(cleaned[:2000])

        try:
            last_heartbeat = 0.0
            while process.poll() is None:
                now = time.monotonic()

                if cancel_requested():
                    self._terminate(process)
                    raise K6CancelledError()

                if now - started > timeout_seconds:
                    self._terminate(process)
                    raise K6ExecutionError(
                        "K6_TIMEOUT",
                        f"Le test k6 a dépassé la limite de {timeout_seconds} secondes.",
                    )

                if now - last_heartbeat >= 2.0:
                    heartbeat()
                    last_heartbeat = now

                flush_output(wait_seconds=0.25)

            output_thread.join(timeout=2.0)
            flush_output()
        finally:
            if process.poll() is None:
                self._terminate(process)
            try:
                if process.stdout is not None:
                    process.stdout.close()
            except OSError:
                pass

        elapsed = max(0.001, time.monotonic() - started)
        exit_code = int(process.returncode or 0)

        points = _read_k6_points(raw_metrics_path)
        metrics, samples, summary = _aggregate_points(
            points,
            configured_duration_seconds=configured_duration,
            sample_interval_seconds=2,
        )

        if int(metrics.get("requests") or 0) <= 0:
            raise K6ExecutionError(
                "K6_NO_REQUESTS",
                "k6 n'a exécuté aucune requête HTTP exploitable.",
            )

        threshold_results = _threshold_results(metrics, run.get("thresholds") or {})
        threshold_failed = any(not item["passed"] for item in threshold_results)

        summary.update(
            {
                "exitCode": exit_code,
                "processElapsedSeconds": round(elapsed, 3),
                "k6Binary": binary_path,
            }
        )

        # Le fichier brut peut devenir volumineux sur un long test. Une fois les
        # métriques et snapshots agrégés, il n'est plus nécessaire à l'interface.
        try:
            raw_metrics_path.unlink(missing_ok=True)
        except OSError:
            pass

        return K6RunResult(
            exit_code=exit_code,
            elapsed_seconds=elapsed,
            metrics=metrics,
            samples=samples,
            threshold_results=threshold_results,
            summary=summary,
            grafana_dashboard_url=grafana_dashboard_url(run),
            threshold_failed=threshold_failed,
        )

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return

        if os.name == "nt":
            try:
                process.terminate()
            except OSError:
                return
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            except OSError:
                try:
                    process.terminate()
                except OSError:
                    return

        try:
            process.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            pass

        if os.name == "nt":
            try:
                process.kill()
            except OSError:
                return
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
            except OSError:
                try:
                    process.kill()
                except OSError:
                    return

        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return
