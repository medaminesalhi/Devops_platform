from __future__ import annotations

import json
import math
import os
import selectors
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from flask import current_app


@dataclass
class K6RunResult:
    exit_code: int
    elapsed_seconds: float
    metrics: dict[str, Any]
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


def _metric_map(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    # Format k6 v2 machine-readable : results.metrics est une liste.
    results = summary.get("results")
    if isinstance(results, dict) and isinstance(results.get("metrics"), list):
        mapped: dict[str, dict[str, Any]] = {}
        for metric in results["metrics"]:
            if isinstance(metric, dict) and metric.get("name"):
                mapped[str(metric["name"])] = metric

        checks = results.get("checks")
        if isinstance(checks, dict) and isinstance(checks.get("metrics"), list):
            for metric in checks["metrics"]:
                if isinstance(metric, dict) and metric.get("name"):
                    mapped[str(metric["name"])] = metric
        return mapped

    # Format historique : metrics est un objet indexé par nom.
    legacy = summary.get("metrics")
    if isinstance(legacy, dict):
        return {
            str(name): metric
            for name, metric in legacy.items()
            if isinstance(metric, dict)
        }
    return {}


def _values(metrics: dict[str, dict[str, Any]], name: str) -> dict[str, Any]:
    metric = metrics.get(name)
    if not isinstance(metric, dict):
        return {}
    values = metric.get("values")
    return values if isinstance(values, dict) else {}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _counter(values: dict[str, Any]) -> int:
    return int(round(_number(values.get("count", values.get("value", 0)))))


def _rate(values: dict[str, Any]) -> float:
    if "rate" in values:
        return _number(values.get("rate"))
    total = _number(values.get("total"))
    matches = _number(values.get("matches"))
    return (matches / total) if total > 0 else 0.0


def _extract_metrics(summary: dict[str, Any], elapsed_seconds: float) -> dict[str, Any]:
    metrics = _metric_map(summary)
    http_reqs = _values(metrics, "http_reqs")
    duration = _values(metrics, "http_req_duration")
    failed = _values(metrics, "http_req_failed")
    iterations = _values(metrics, "iterations")
    received = _values(metrics, "data_received")
    sent = _values(metrics, "data_sent")

    checks = _values(metrics, "checks")
    if not checks:
        checks = _values(metrics, "checks_succeeded")

    request_count = _counter(http_reqs)
    rps = _number(http_reqs.get("rate"))
    if rps <= 0 and elapsed_seconds > 0:
        rps = request_count / elapsed_seconds

    return {
        "requests": request_count,
        "rps": round(rps, 3),
        "avgMs": round(_number(duration.get("avg")), 3),
        "minMs": round(_number(duration.get("min")), 3),
        "maxMs": round(_number(duration.get("max")), 3),
        "p90Ms": round(_number(duration.get("p(90)")), 3),
        "p95Ms": round(_number(duration.get("p(95)")), 3),
        "p99Ms": round(_number(duration.get("p(99)")), 3),
        "errorRatePercent": round(_rate(failed) * 100.0, 4),
        "checksRatePercent": round(_rate(checks) * 100.0, 4),
        "dataReceivedBytes": _counter(received),
        "dataSentBytes": _counter(sent),
        "iterations": _counter(iterations),
    }


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
    namespace = str(observability.get("namespace") or "").strip()
    if not namespace:
        raise K6ExecutionError(
            "OBSERVABILITY_NAMESPACE_MISSING",
            "Le namespace Prometheus/Grafana est absent.",
        )

    template = str(
        current_app.config.get(
            "PERFORMANCE_PROMETHEUS_REMOTE_WRITE_URL_TEMPLATE",
            "http://sapixi-k6-prometheus.{namespace}.svc.cluster.local:9090/api/v1/write",
        )
    )
    return template.format(namespace=namespace)


def grafana_dashboard_url(run: dict[str, Any]) -> str | None:
    if run.get("mode") != "observability":
        return None

    observability = run.get("observability") or {}
    if not bool(observability.get("installGrafana", True)):
        return None

    host = str(observability.get("grafanaIngressHost") or "").strip()
    if not host:
        return None

    scheme = str(current_app.config.get("PERFORMANCE_GRAFANA_SCHEME", "https")).strip() or "https"
    dashboard_uid = str(
        current_app.config.get("PERFORMANCE_GRAFANA_DASHBOARD_UID", "k6-performance")
    ).strip() or "k6-performance"

    return (
        f"{scheme}://{host}/d/{quote(dashboard_uid, safe='')}"
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
        if shutil.which(self.binary) is None:
            raise K6ExecutionError(
                "K6_UNAVAILABLE",
                f"Le binaire k6 '{self.binary}' est introuvable dans le worker.",
            )

        run_id = int(run["id"])
        workspace = self.workspace_root / str(run_id)
        workspace.mkdir(parents=True, exist_ok=True)

        script_path = workspace / "script.js"
        summary_path = workspace / "summary.json"
        script_path.write_text(_build_script(run), encoding="utf-8")

        command = [
            self.binary,
            "run",
            "--summary-mode=compact",
            f"--summary-export={summary_path}",
        ]

        remote_write_url = prometheus_remote_write_url(run)
        environment = os.environ.copy()
        environment["K6_NO_USAGE_REPORT"] = "true"

        if remote_write_url:
            command.extend(["--out", "experimental-prometheus-rw"])
            environment["K6_PROMETHEUS_RW_SERVER_URL"] = remote_write_url
            environment["K6_PROMETHEUS_RW_TREND_STATS"] = "p(90),p(95),p(99),min,max,avg"

        command.append(str(script_path))

        timeout_seconds = (
            int((run.get("load_profile") or {}).get("durationSeconds") or 1)
            + int(current_app.config.get("PERFORMANCE_RUN_GRACE_SECONDS", 120))
        )

        started = time.monotonic()
        process = subprocess.Popen(
            command,
            cwd=str(workspace),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            shell=False,
            start_new_session=True,
        )

        selector = selectors.DefaultSelector()
        if process.stdout is not None:
            selector.register(process.stdout, selectors.EVENT_READ)

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

                for key, _ in selector.select(timeout=0.5):
                    line = key.fileobj.readline()
                    if line:
                        cleaned = line.strip()
                        if cleaned:
                            log_line(cleaned[:2000])

            if process.stdout is not None:
                for line in process.stdout:
                    cleaned = line.strip()
                    if cleaned:
                        log_line(cleaned[:2000])
        finally:
            selector.close()
            if process.poll() is None:
                self._terminate(process)

        elapsed = max(0.001, time.monotonic() - started)
        exit_code = int(process.returncode or 0)

        if not summary_path.exists():
            raise K6ExecutionError(
                "K6_SUMMARY_MISSING",
                "k6 n'a pas produit le fichier summary.json.",
            )

        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise K6ExecutionError(
                "K6_SUMMARY_INVALID",
                "Le fichier summary.json produit par k6 est invalide.",
            ) from error

        if not isinstance(summary, dict):
            raise K6ExecutionError(
                "K6_SUMMARY_INVALID",
                "Le résumé k6 ne contient pas un objet JSON valide.",
            )

        metrics = _extract_metrics(summary, elapsed)
        threshold_results = _threshold_results(metrics, run.get("thresholds") or {})
        threshold_failed = any(not item["passed"] for item in threshold_results)

        return K6RunResult(
            exit_code=exit_code,
            elapsed_seconds=elapsed,
            metrics=metrics,
            threshold_results=threshold_results,
            summary=summary,
            grafana_dashboard_url=grafana_dashboard_url(run),
            threshold_failed=threshold_failed,
        )

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return

        try:
            process.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            pass

        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        process.wait(timeout=5)
