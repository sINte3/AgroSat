"""Thread-safe, low-cardinality process metrics with Prometheus rendering."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import logging
import re
import threading
import time
from typing import Any


API_DURATION_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
DB_DURATION_BUCKETS = (0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1)
LABEL_PATTERN = re.compile(r"^[A-Za-z0-9_./{}:-]{1,160}$")
DB_OPERATIONS = {"select", "insert", "update", "delete", "other"}
CACHE_OPERATIONS = {"get", "get_binary", "set", "set_binary", "delete", "delete_pattern", "probe"}
CACHE_RESULTS = {"hit", "miss", "success", "failure", "unavailable", "bypass"}
logger = logging.getLogger(__name__)


def _label(value: Any) -> str:
    candidate = str(value or "unknown").lower()
    return candidate if LABEL_PATTERN.fullmatch(candidate) else "other"


def _labels(values: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(key), _label(value)) for key, value in values.items()))


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_labels(values: tuple[tuple[str, str], ...]) -> str:
    if not values:
        return ""
    rendered = ",".join(f'{key}="{_escape_label(value)}"' for key, value in values)
    return "{" + rendered + "}"


@dataclass
class Histogram:
    buckets: tuple[float, ...]
    counts: list[int] = field(default_factory=list)
    count: int = 0
    total: float = 0.0

    def __post_init__(self):
        if not self.counts:
            self.counts = [0 for _ in self.buckets]

    def observe(self, value: float) -> None:
        self.count += 1
        self.total += value
        for index, boundary in enumerate(self.buckets):
            if value <= boundary:
                self.counts[index] += 1


class MetricRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._counters: dict[
            tuple[str, tuple[tuple[str, str], ...]],
            float,
        ] = defaultdict(float)
        self._histograms: dict[
            tuple[str, tuple[tuple[str, str], ...]],
            Histogram,
        ] = {}

    def increment(
        self,
        name: str,
        *,
        labels: dict[str, Any],
        value: float = 1,
    ) -> None:
        key = (name, _labels(labels))
        with self._lock:
            self._counters[key] += value

    def observe(
        self,
        name: str,
        value: float,
        *,
        labels: dict[str, Any],
        buckets: tuple[float, ...],
    ) -> None:
        key = (name, _labels(labels))
        with self._lock:
            histogram = self._histograms.setdefault(key, Histogram(buckets))
            histogram.observe(max(0.0, float(value)))

    def render(self) -> str:
        with self._lock:
            counters = sorted(self._counters.items())
            histograms = sorted(self._histograms.items())
        lines = []
        for (name, labels), value in counters:
            lines.append(f"{name}{_format_labels(labels)} {value:g}")
        for (name, labels), histogram in histograms:
            for boundary, count in zip(histogram.buckets, histogram.counts):
                bucket_labels = tuple(
                    sorted((*labels, ("le", f"{boundary:g}")))
                )
                lines.append(
                    f"{name}_bucket{_format_labels(bucket_labels)} {count}"
                )
            infinity_labels = tuple(sorted((*labels, ("le", "+Inf"))))
            lines.append(
                f"{name}_bucket{_format_labels(infinity_labels)} "
                f"{histogram.count}"
            )
            lines.append(f"{name}_count{_format_labels(labels)} {histogram.count}")
            lines.append(f"{name}_sum{_format_labels(labels)} {histogram.total:g}")
        return "\n".join(lines) + ("\n" if lines else "")


REGISTRY = MetricRegistry()


def record_api_request(method: str, route: str, status: int, duration: float) -> None:
    labels = {
        "method": method,
        "route": route,
        "status_class": f"{int(status) // 100}xx",
    }
    REGISTRY.increment("agrosat_api_requests_total", labels=labels)
    REGISTRY.observe(
        "agrosat_api_request_duration_seconds",
        duration,
        labels={"method": method, "route": route},
        buckets=API_DURATION_BUCKETS,
    )


def _db_operation(statement: str) -> str:
    first = str(statement or "").lstrip().split(maxsplit=1)
    operation = first[0].lower() if first else "other"
    return operation if operation in DB_OPERATIONS else "other"


def record_db_statement(statement: str, duration: float, outcome: str) -> None:
    operation = _db_operation(statement)
    labels = {"operation": operation, "outcome": outcome}
    REGISTRY.increment("agrosat_db_statements_total", labels=labels)
    REGISTRY.observe(
        "agrosat_db_statement_duration_seconds",
        duration,
        labels=labels,
        buckets=DB_DURATION_BUCKETS,
    )


def instrument_engine(engine, *, registry: MetricRegistry = REGISTRY) -> None:
    if getattr(engine, "_agrosat_metrics_instrumented", False):
        return
    from sqlalchemy import event

    @event.listens_for(engine, "before_cursor_execute")
    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        context._agrosat_metrics_started = time.perf_counter()
        context._agrosat_metrics_statement = statement

    @event.listens_for(engine, "after_cursor_execute")
    def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        started = getattr(context, "_agrosat_metrics_started", time.perf_counter())
        operation = getattr(context, "_agrosat_metrics_statement", statement)
        duration = time.perf_counter() - started
        labels = {"operation": _db_operation(operation), "outcome": "success"}
        registry.increment("agrosat_db_statements_total", labels=labels)
        registry.observe(
            "agrosat_db_statement_duration_seconds",
            duration,
            labels=labels,
            buckets=DB_DURATION_BUCKETS,
        )

    @event.listens_for(engine, "handle_error")
    def handle_error(exception_context):
        context = exception_context.execution_context
        started = getattr(context, "_agrosat_metrics_started", time.perf_counter())
        statement = getattr(
            context,
            "_agrosat_metrics_statement",
            exception_context.statement,
        )
        duration = time.perf_counter() - started
        labels = {"operation": _db_operation(statement), "outcome": "failure"}
        registry.increment("agrosat_db_statements_total", labels=labels)
        registry.observe(
            "agrosat_db_statement_duration_seconds",
            duration,
            labels=labels,
            buckets=DB_DURATION_BUCKETS,
        )

    engine._agrosat_metrics_instrumented = True


def record_cache_operation(operation: str, result: str) -> None:
    safe_operation = operation if operation in CACHE_OPERATIONS else "other"
    safe_result = result if result in CACHE_RESULTS else "failure"
    REGISTRY.increment(
        "agrosat_cache_operations_total",
        labels={"operation": safe_operation, "result": safe_result},
    )


class MetricsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status = 500

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = scope.get("route")
            route_template = getattr(route, "path", "unmatched")
            duration = time.perf_counter() - started
            record_api_request(
                scope.get("method", "unknown"),
                route_template,
                status,
                duration,
            )
            logger.info(
                "api_request_complete",
                extra={
                    "agrosat_context": {
                        "method": scope.get("method", "unknown"),
                        "route": route_template,
                        "status_class": f"{status // 100}xx",
                        "duration_ms": round(duration * 1000, 3),
                    }
                },
            )


def collector_metrics(collector: dict[str, Any]) -> str:
    latest = collector.get("latest") if isinstance(collector, dict) else None
    if not isinstance(latest, dict):
        return ""
    status = _label(latest.get("status"))
    failure = _label(latest.get("failure_category") or "none")
    duration = latest.get("duration_seconds")
    lines = []
    if isinstance(duration, (int, float)) and 0 <= duration <= 21600:
        lines.append(
            "agrosat_collector_last_run_duration_seconds"
            f'{{failure_category="{failure}",status="{status}"}} {duration:g}'
        )
    for provider in latest.get("providers", []):
        counters = provider.get("counters")
        if not isinstance(counters, dict):
            continue
        provider_name = _label(provider.get("provider"))
        for outcome, value in sorted(counters.items()):
            lines.append(
                "agrosat_collector_last_run_fields"
                f'{{outcome="{_label(outcome)}",provider="{provider_name}"}} '
                f"{value}"
            )
    return "\n".join(lines) + ("\n" if lines else "")
