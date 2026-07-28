"""Bounded, deterministic HTTP load harness for isolated AgroSat runtimes."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile
import time
import tracemalloc
from typing import Any
from urllib.parse import urlparse
import uuid

import httpx


MAX_CONCURRENCY = 100
MAX_REQUESTS = 10_000
MAX_TIMEOUT_SECONDS = 60
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_SCENARIOS = 100
ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH"}
AUTH_ENV_NAME = "TASK209_LOAD_AUTH_TOKEN"


class LoadContractError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def validate_base_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise LoadContractError("base URL must be credential-free loopback HTTP(S)")
    return value.rstrip("/")


def load_scenarios(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LoadContractError("scenario file is unavailable or malformed") from exc
    scenarios = payload.get("scenarios") if isinstance(payload, dict) else None
    if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= MAX_SCENARIOS:
        raise LoadContractError("scenario count is invalid")
    names = set()
    validated = []
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise LoadContractError("scenario must be an object")
        name = scenario.get("name")
        method = str(scenario.get("method", "")).upper()
        path_value = scenario.get("path")
        expected = scenario.get("expected_statuses")
        enabled = scenario.get("enabled", True)
        is_write = scenario.get("write", False)
        if (
            not isinstance(name, str)
            or not 1 <= len(name) <= 80
            or name in names
            or method not in ALLOWED_METHODS
            or not isinstance(path_value, str)
            or not path_value.startswith("/")
            or path_value.startswith("//")
            or "://" in path_value
            or not isinstance(expected, list)
            or not expected
            or any(type(code) is not int or not 100 <= code <= 599 for code in expected)
            or type(enabled) is not bool
            or type(is_write) is not bool
        ):
            raise LoadContractError("scenario contract is invalid")
        body = scenario.get("json")
        if body is not None:
            encoded = json.dumps(body, ensure_ascii=False)
            if len(encoded.encode("utf-8")) > 64 * 1024:
                raise LoadContractError("scenario body exceeds 64 KiB")
        if method == "GET" and is_write:
            raise LoadContractError("GET scenario cannot be marked write")
        names.add(name)
        validated.append(
            {
                "name": name,
                "method": method,
                "path": path_value,
                "expected_statuses": expected,
                "enabled": enabled,
                "write": is_write,
                "json": body,
                "blocked_reason": str(scenario.get("blocked_reason", ""))[:240],
            }
        )
    return validated


def percentile(values: list[float], percent: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil((percent / 100) * len(ordered)) - 1)
    return round(ordered[index], 3)


async def execute_load(
    *,
    base_url: str,
    scenarios: list[dict[str, Any]],
    concurrency: int,
    request_count: int,
    timeout_seconds: float,
    allow_writes: bool,
    token: str | None,
    transport=None,
) -> dict[str, Any]:
    if not 1 <= concurrency <= MAX_CONCURRENCY:
        raise LoadContractError("concurrency is outside the bounded range")
    if not 1 <= request_count <= MAX_REQUESTS:
        raise LoadContractError("request count is outside the bounded range")
    if not 0.1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise LoadContractError("timeout is outside the bounded range")
    enabled = [scenario for scenario in scenarios if scenario["enabled"]]
    if not enabled:
        raise LoadContractError("at least one enabled scenario is required")
    if any(scenario["write"] for scenario in enabled) and not allow_writes:
        raise LoadContractError("write scenarios require explicit --allow-writes")

    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    semaphore = asyncio.Semaphore(concurrency)
    samples: dict[str, list[float]] = {scenario["name"]: [] for scenario in enabled}
    statuses: dict[str, Counter] = {
        scenario["name"]: Counter() for scenario in enabled
    }
    errors: Counter = Counter()
    run_id = uuid.uuid4().hex
    scenario_positions = {
        scenario["name"]: position for position, scenario in enumerate(enabled)
    }
    started_at = utc_now()
    wall_started = time.perf_counter()
    cpu_started = time.process_time()
    tracing_was_active = tracemalloc.is_tracing()
    if not tracing_was_active:
        tracemalloc.start()
    memory_started, _ = tracemalloc.get_traced_memory()

    try:
        async with httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=timeout_seconds,
            transport=transport,
            follow_redirects=False,
        ) as client:
            async def one(index: int) -> None:
                scenario = enabled[index % len(enabled)]
                request_headers = {}
                if scenario["write"]:
                    scenario_position = scenario_positions[scenario["name"]]
                    request_headers["Idempotency-Key"] = (
                        f"task209-load-{run_id}-{scenario_position}"
                    )
                request_started = time.perf_counter()
                try:
                    async with semaphore:
                        response = await client.request(
                            scenario["method"],
                            scenario["path"],
                            json=scenario["json"],
                            headers=request_headers,
                        )
                    elapsed = (time.perf_counter() - request_started) * 1000
                    samples[scenario["name"]].append(elapsed)
                    statuses[scenario["name"]][str(response.status_code)] += 1
                    if len(response.content) > MAX_RESPONSE_BYTES:
                        errors["response_too_large"] += 1
                    elif response.status_code not in scenario["expected_statuses"]:
                        errors["unexpected_status"] += 1
                except httpx.TimeoutException:
                    errors["timeout"] += 1
                except httpx.HTTPError:
                    errors["transport"] += 1

            await asyncio.gather(*(one(index) for index in range(request_count)))
    finally:
        _, memory_peak = tracemalloc.get_traced_memory()
        if not tracing_was_active:
            tracemalloc.stop()

    duration = time.perf_counter() - wall_started
    cpu_seconds = time.process_time() - cpu_started
    completed = sum(len(value) for value in samples.values())
    scenario_results = []
    all_samples = []
    for scenario in enabled:
        values = samples[scenario["name"]]
        all_samples.extend(values)
        scenario_results.append(
            {
                "name": scenario["name"],
                "method": scenario["method"],
                "path": scenario["path"],
                "write": scenario["write"],
                "requests_completed": len(values),
                "status_counts": dict(sorted(statuses[scenario["name"]].items())),
                "p50_ms": percentile(values, 50),
                "p95_ms": percentile(values, 95),
                "p99_ms": percentile(values, 99),
            }
        )
    return {
        "schema_version": 1,
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": utc_now(),
        "base_target": "loopback",
        "concurrency": concurrency,
        "requests_planned": request_count,
        "requests_completed": completed,
        "duration_seconds": round(duration, 3),
        "requests_per_second": round(completed / duration, 3) if duration else None,
        "p50_ms": percentile(all_samples, 50),
        "p95_ms": percentile(all_samples, 95),
        "p99_ms": percentile(all_samples, 99),
        "error_counts": dict(sorted(errors.items())),
        "error_rate": round(sum(errors.values()) / request_count, 6),
        "authorization_configured": bool(token),
        "writes_enabled": allow_writes,
        "scenarios": scenario_results,
        "client_resource_metrics": {
            "cpu_seconds": round(cpu_seconds, 6),
            "python_peak_memory_bytes": max(0, memory_peak - memory_started),
            "scope": "load client process only",
        },
        "server_resource_metrics": None,
        "server_metrics_limitation": (
            "server CPU, memory, DB connections, and query counts require "
            "isolated runtime instrumentation"
        ),
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--scenarios", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--timeout-seconds", type=float, default=10)
    parser.add_argument("--allow-writes", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        base_url = validate_base_url(args.base_url)
        scenario_path = Path(args.scenarios)
        output_path = Path(args.output)
        if not scenario_path.is_absolute() or not output_path.is_absolute():
            raise LoadContractError("scenario and output paths must be absolute")
        scenarios = load_scenarios(scenario_path)
        if args.validate_only:
            report = {
                "schema_version": 1,
                "status": "validated",
                "scenario_count": len(scenarios),
                "enabled_count": sum(item["enabled"] for item in scenarios),
                "write_count": sum(item["write"] for item in scenarios),
            }
        else:
            report = asyncio.run(
                execute_load(
                    base_url=base_url,
                    scenarios=scenarios,
                    concurrency=args.concurrency,
                    request_count=args.requests,
                    timeout_seconds=args.timeout_seconds,
                    allow_writes=args.allow_writes,
                    token=os.environ.get(AUTH_ENV_NAME),
                )
            )
        atomic_json(output_path, report)
        return 0
    except LoadContractError:
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception:
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
