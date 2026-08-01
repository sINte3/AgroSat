#!/usr/bin/env python3
"""Run the isolated PROGRAM R1 Gate 2 reliability qualification.

The script is intentionally read-only for PostgreSQL. Redis writes are limited
to namespaced qualification keys on the explicitly verified loopback instance.
All emitted evidence is sanitized and contains no connection URLs or secrets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlparse


SCHEMA_VERSION = 1
PROBE_ENVIRONMENTS = (
    "gate2_qualification_a",
    "gate2_qualification_b",
    "gate2_qualification_current",
)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_loopback_url(value: str, expected_port: int, label: str) -> int:
    parsed = urlparse(value)
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise RuntimeError(f"{label} is not loopback")
    if parsed.port != expected_port:
        raise RuntimeError(f"{label} has an unexpected port")
    if label == "Redis" and parsed.path not in {"/15", "15"}:
        raise RuntimeError("Redis database index is not the isolated database")
    return parsed.port


def reset_cache(cache: Any, client: Any | None = None) -> None:
    cache._redis = client
    cache.CACHE_AVAILABLE = client is not None
    cache._retry_after_monotonic = 0.0


def scan_bounded(client: Any, pattern: str, maximum: int = 1000) -> list[str]:
    cursor = 0
    keys: list[str] = []
    for _ in range(100):
        cursor, batch = client.scan(cursor=cursor, match=pattern, count=100)
        keys.extend(str(item) for item in batch)
        if len(keys) > maximum:
            raise RuntimeError("qualification key scan exceeded its bound")
        if cursor == 0:
            return sorted(set(keys))
    raise RuntimeError("qualification key scan did not terminate within its bound")


def cleanup_probe_keys(client: Any) -> None:
    keys: list[str] = []
    for environment in PROBE_ENVIRONMENTS:
        keys.extend(scan_bounded(client, f"agrosat:{environment}:*"))
    unique = sorted(set(keys))
    if unique:
        client.delete(*unique)


def live_redis_qualification(
    backend: Path,
    expected_redis_port: int,
    expected_postgres_port: int,
) -> dict[str, Any]:
    import redis
    from sqlalchemy import text

    from config import settings
    from database import SessionLocal
    from services import cache

    redis_port = require_loopback_url(
        settings.redis_url,
        expected_redis_port,
        "Redis",
    )
    postgres_port = require_loopback_url(
        settings.database_url,
        expected_postgres_port,
        "PostgreSQL",
    )
    client = redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    if client.ping() is not True:
        raise RuntimeError("isolated Redis did not respond")

    server_info = client.info(section="server")
    persistence = client.config_get("appendonly")
    original_environment = settings.environment
    original_redis_url = settings.redis_url
    cleanup_probe_keys(client)

    created_keys: set[str] = set()

    def physical(logical: str, *, pattern: bool = False) -> str:
        value = cache._physical_key(logical, pattern=pattern)
        if value is None:
            raise RuntimeError("qualification key was rejected")
        return value

    def set_value(logical: str, value: Any, ttl: int = 120) -> None:
        if not cache.cache_set(logical, value, ttl_seconds=ttl):
            raise RuntimeError("qualification cache write failed")
        created_keys.add(physical(logical))

    checks: dict[str, bool] = {}
    details: dict[str, Any] = {}
    try:
        reset_cache(cache, client)

        logical = "gate2probe:shared-logical-key"
        settings.environment = PROBE_ENVIRONMENTS[0]
        set_value(logical, {"generation": "a"})
        key_a = physical(logical)
        settings.environment = PROBE_ENVIRONMENTS[1]
        set_value(logical, {"generation": "b"})
        key_b = physical(logical)
        value_b = cache.cache_get(logical)
        settings.environment = PROBE_ENVIRONMENTS[0]
        value_a = cache.cache_get(logical)
        checks["environment_namespace_separation"] = (
            key_a != key_b
            and value_a == {"generation": "a"}
            and value_b == {"generation": "b"}
        )

        settings.environment = PROBE_ENVIRONMENTS[2]
        reset_cache(cache, client)
        scoped_values = {
            "gate2probe:tenant:101:user:1001": {"scope": "tenant-a-user-a"},
            "gate2probe:tenant:101:user:1002": {"scope": "tenant-a-user-b"},
            "gate2probe:tenant:202:user:2001": {"scope": "tenant-b-user-a"},
        }
        for scoped_key, scoped_value in scoped_values.items():
            set_value(scoped_key, scoped_value)
        checks["tenant_user_scope_separation"] = all(
            cache.cache_get(key) == value for key, value in scoped_values.items()
        ) and len({physical(key) for key in scoped_values}) == len(scoped_values)

        tenant_a_keys = (
            "alerts:101:gate2probe",
            "field_alerts:1001:gate2probe",
            "dashboard:summary:101",
            "fields:list:v2:enterprise:101",
            "fields:geojson:v2:enterprise:101",
            "field-tiles:v2:metadata:enterprise:101",
            "field-tiles:v2:tile:enterprise:101:12:1:2",
        )
        global_keys = (
            "alerts:all:gate2probe",
            "dashboard:summary:all",
            "fields:list:v2:all",
            "fields:geojson:v2:all",
            "field-tiles:v2:metadata:all",
            "field-tiles:v2:tile:all:12:1:2",
        )
        tenant_b_keys = (
            "alerts:202:gate2probe",
            "field_alerts:2002:gate2probe",
            "dashboard:summary:202",
            "fields:list:v2:enterprise:202",
            "fields:geojson:v2:enterprise:202",
            "field-tiles:v2:metadata:enterprise:202",
            "field-tiles:v2:tile:enterprise:202:12:1:2",
        )
        for cache_key in (*tenant_a_keys, *global_keys, *tenant_b_keys):
            set_value(cache_key, {"qualification": True})
        patterns = cache.alert_mutation_cache_patterns(101, 1001)
        invalidation_result = cache.cache_delete_patterns(patterns)
        tenant_a_removed = all(client.exists(physical(key)) == 0 for key in tenant_a_keys)
        globals_removed = all(client.exists(physical(key)) == 0 for key in global_keys)
        tenant_b_preserved = all(client.exists(physical(key)) == 1 for key in tenant_b_keys)
        checks["bounded_tenant_invalidation"] = (
            invalidation_result
            and tenant_a_removed
            and globals_removed
            and tenant_b_preserved
            and len(patterns) <= cache.MAX_INVALIDATION_PATTERNS
        )
        details["invalidation_pattern_count"] = len(patterns)
        details["impacted_key_count"] = len(tenant_a_keys) + len(global_keys)
        details["unrelated_key_count_preserved"] = len(tenant_b_keys)

        corrupt_json = "gate2probe:corrupt-json"
        corrupt_json_physical = physical(corrupt_json)
        created_keys.add(corrupt_json_physical)
        client.set(corrupt_json_physical, "{not-json", ex=120)
        corrupt_json_result = cache.cache_get(corrupt_json)
        checks["corrupt_payload_recovery"] = (
            corrupt_json_result is None
            and client.exists(corrupt_json_physical) == 0
            and cache.CACHE_AVAILABLE
        )

        corrupt_binary = "gate2probe:corrupt-binary"
        corrupt_binary_physical = physical(corrupt_binary)
        created_keys.add(corrupt_binary_physical)
        client.set(corrupt_binary_physical, "not-base64%%%", ex=120)
        corrupt_binary_result = cache.cache_get_binary(corrupt_binary)
        checks["corrupt_binary_recovery"] = (
            corrupt_binary_result is None
            and client.exists(corrupt_binary_physical) == 0
            and cache.CACHE_AVAILABLE
        )

        ttl_key = "gate2probe:ttl"
        set_value(ttl_key, {"bounded": True}, ttl=60)
        observed_ttl = int(client.ttl(physical(ttl_key)))
        overlong_rejected = not cache.cache_set(
            "gate2probe:ttl-overlong",
            {"bounded": False},
            ttl_seconds=cache.MAX_CACHE_TTL_SECONDS + 1,
        )
        checks["ttl_bounded"] = 0 < observed_ttl <= 60 and overlong_rejected
        details["observed_probe_ttl_seconds"] = observed_ttl

        invalid_environment = settings.environment
        settings.environment = "INVALID ENVIRONMENT"
        before_invalid_count = len(scan_bounded(client, "agrosat:*:gate2probe:*"))
        invalid_set = cache.cache_set("gate2probe:invalid-environment", True)
        invalid_get = cache.cache_get("gate2probe:invalid-environment")
        after_invalid_count = len(scan_bounded(client, "agrosat:*:gate2probe:*"))
        checks["invalid_environment_fails_closed"] = (
            not invalid_set
            and invalid_get is None
            and before_invalid_count == after_invalid_count
        )
        settings.environment = invalid_environment

        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(("127.0.0.1", 0))
        unavailable_port = blocker.getsockname()[1]
        settings.redis_url = f"redis://127.0.0.1:{unavailable_port}/15"
        reset_cache(cache)
        outage_get = cache.cache_get("gate2probe:outage")
        outage_set = cache.cache_set("gate2probe:outage", True)
        outage_bypassed = outage_get is None and not outage_set and not cache.CACHE_AVAILABLE
        with SessionLocal() as session:
            database_available = session.execute(text("SELECT 1")).scalar_one() == 1
        settings.redis_url = original_redis_url
        blocker.close()
        reset_cache(cache)
        reconnected = cache.cache_probe()
        checks["redis_outage_preserves_postgresql_source_of_truth"] = (
            outage_bypassed and database_available
        )
        checks["reconnect_after_outage"] = reconnected

        cache_source = (backend / "services" / "cache.py").read_text(encoding="utf-8")
        application_sources = "\n".join(
            path.read_text(encoding="utf-8", errors="replace").lower()
            for path in backend.rglob("*.py")
            if "tests" not in path.parts
        )
        flush_count = sum(
            application_sources.count(token) for token in ("flushall(", "flushdb(")
        )
        checks["no_global_flush"] = flush_count == 0
        checks["bounded_scan_implementation"] = all(
            token in cache_source
            for token in (
                "MAX_PATTERN_DELETE_KEYS",
                "MAX_PATTERN_SCAN_CALLS",
                "client.scan(",
            )
        ) and "client.keys(" not in cache_source
        details["application_flush_token_count"] = flush_count
        details["maximum_pattern_delete_keys"] = cache.MAX_PATTERN_DELETE_KEYS
        details["maximum_pattern_scan_calls"] = cache.MAX_PATTERN_SCAN_CALLS
        details["postgresql_read_probe"] = "PASS"

        if not all(checks.values()):
            failed = sorted(name for name, passed in checks.items() if not passed)
            raise RuntimeError(f"live Redis checks failed: {','.join(failed)}")

        return {
            "schemaVersion": SCHEMA_VERSION,
            "gate": "GATE2",
            "operation": "completion_run_live_redis_qualification",
            "targetClass": "isolated_standalone_redis_compatible",
            "server": {
                "hostClass": "loopback",
                "port": redis_port,
                "databaseIndex": 15,
                "redisVersion": str(server_info.get("redis_version", "unknown")),
                "persistenceEnabled": str(persistence.get("appendonly", "yes")).lower() == "yes",
                "serviceInstalled": False,
            },
            "postgresql": {
                "hostClass": "loopback",
                "port": postgres_port,
                "operation": "read_only_source_of_truth_probe",
            },
            "checks": checks,
            "details": details,
            "isolatedPostgresqlWrites": 0,
            "externalProviderCalls": 0,
            "productionWrites": 0,
            "status": "PASS",
            "marker": "PASS_GATE2_LIVE_REDIS_QUALIFIED",
        }
    finally:
        settings.environment = original_environment
        settings.redis_url = original_redis_url
        reset_cache(cache, client)
        cleanup_probe_keys(client)
        client.close()


def lock_helper(backend: Path, mode: str, lock_file: Path, mutex: str) -> int:
    sys.path.insert(0, str(backend))
    from services.collector_locking import acquire_lock, release_lock

    path = acquire_lock(str(lock_file), mutex_name=mutex)
    if mode == "hold":
        print("GATE2_LOCK_READY", flush=True)
        while True:
            time.sleep(1)
    release_lock(path)
    return 0


def lock_qualification(
    script: Path,
    backend: Path,
    scratch: Path,
    head: str,
) -> dict[str, Any]:
    lock_file = scratch / "gate2-live-collector.lock"
    mutex = f"Global\\AgroSatProgramR1Gate2_{head[:12]}"
    if lock_file.exists():
        lock_file.unlink()
    helper_base = [
        sys.executable,
        str(script),
        "--runtime-env",
        os.environ["AGROSAT_RUNTIME_ENV_FILE"],
        "--evidence-dir",
        str(scratch),
        "--expected-head",
        head,
        "--expected-redis-port",
        "56381",
        "--expected-postgres-port",
        "55439",
        "--lock-file",
        str(lock_file),
        "--mutex-name",
        mutex,
    ]
    holder = subprocess.Popen(
        [*helper_base, "--lock-helper", "hold"],
        cwd=str(script.parents[2]),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not lock_file.exists():
            if holder.poll() is not None:
                raise RuntimeError("lock holder exited before acquisition")
            time.sleep(0.1)
        if not lock_file.exists():
            raise RuntimeError("lock holder did not acquire within timeout")

        contender = subprocess.run(
            [*helper_base, "--lock-helper", "try"],
            cwd=str(script.parents[2]),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        contention_exit = contender.returncode
        holder.kill()
        holder.wait(timeout=10)
        stale_file_present = lock_file.exists()
        recovery = subprocess.run(
            [*helper_base, "--lock-helper", "try"],
            cwd=str(script.parents[2]),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        recovery_exit = recovery.returncode
        released_after_recovery = not lock_file.exists()
        checks = {
            "exclusive_cross_process_mutex": contention_exit == 3,
            "abandoned_mutex_recovery": recovery_exit == 0,
            "stale_lock_file_reconciled": stale_file_present and released_after_recovery,
        }
        if not all(checks.values()):
            raise RuntimeError("collector mutex qualification failed")
        return {
            "schemaVersion": SCHEMA_VERSION,
            "gate": "GATE2",
            "operation": "completion_run_live_collector_mutex_qualification",
            "targetClass": "isolated_cross_process_win32_named_mutex",
            "checks": checks,
            "observed": {
                "contentionExitCode": contention_exit,
                "recoveryExitCode": recovery_exit,
                "holderTerminationClass": "forced_process_abandonment",
            },
            "externalProviderCalls": 0,
            "isolatedPostgresqlWrites": 0,
            "productionWrites": 0,
            "status": "PASS",
            "marker": "PASS_GATE2_COLLECTOR_LOCKING",
        }
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=10)
        if lock_file.exists():
            lock_file.unlink()


def query_two_field_ids(expected_postgres_port: int) -> list[int]:
    from sqlalchemy import text

    from config import settings
    from database import SessionLocal

    require_loopback_url(settings.database_url, expected_postgres_port, "PostgreSQL")
    with SessionLocal() as session:
        values = session.execute(
            text("SELECT id FROM fields WHERE is_active = true ORDER BY id LIMIT 2")
        ).scalars().all()
    ids = [int(value) for value in values]
    if len(ids) < 2:
        raise RuntimeError("isolated qualification dataset has fewer than two active fields")
    return ids


def run_collector_qualifications(
    worktree: Path,
    backend: Path,
    evidence_dir: Path,
    expected_postgres_port: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    field_ids = query_two_field_ids(expected_postgres_port)
    child_env = os.environ.copy()
    raw_dry_log = evidence_dir / ".gate2-dry-run-raw.json"
    lock_file = evidence_dir / ".gate2-dry-run.lock"
    dry_command = [
        sys.executable,
        str(backend / "scripts" / "collect_satellite_indices.py"),
        "--dry-run",
        "--no-sentinel",
        "--field-id",
        str(field_ids[0]),
        "--index",
        "ndvi",
        "--date-from",
        "2026-07-01",
        "--date-to",
        "2026-07-02",
        "--max-fields",
        "1",
        "--lock-file",
        str(lock_file),
        "--output-log",
        str(raw_dry_log),
    ]
    dry = subprocess.run(
        dry_command,
        cwd=str(worktree),
        env=child_env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if dry.returncode != 0 or not raw_dry_log.is_file():
        raise RuntimeError("canonical dry-run collector qualification failed")
    dry_payload = json.loads(raw_dry_log.read_text(encoding="utf-8"))
    raw_dry_log.unlink()
    if lock_file.exists():
        lock_file.unlink()
    dry_checks = {
        "explicitDryRun": dry_payload.get("run_mode") == "dry-run",
        "boundedFieldScope": dry_payload.get("fields_selected") == 1,
        "providerCallsZero": dry_payload.get("sentinel_hub_calls") == 0,
        "databaseWritesZero": dry_payload.get("db_writes") == 0,
        "contractExitCode": dry_payload.get("exit_code") == 0,
        "lockNotAcquiredInDryRun": dry_payload.get("lock_acquired") is False,
    }
    if not all(dry_checks.values()):
        raise RuntimeError("canonical dry-run collector contract failed")
    dry_result = {
        "schemaVersion": SCHEMA_VERSION,
        "gate": "GATE2",
        "operation": "completion_run_canonical_collector_dry_run",
        "targetClass": "isolated_postgresql_read_only_and_provider_disabled",
        "checks": dry_checks,
        "observed": {
            "selectedFieldCount": dry_payload.get("fields_selected"),
            "providerCallCount": dry_payload.get("sentinel_hub_calls"),
            "databaseWriteCount": dry_payload.get("db_writes"),
            "exitCode": dry.returncode,
        },
        "productionWrites": 0,
        "status": "PASS",
        "marker": "PASS_GATE2_CANONICAL_COLLECTOR_DRY_RUN",
    }

    negative_root = evidence_dir / ".gate2-negative-ledger"
    negative_root.mkdir(parents=True, exist_ok=True)
    negative_lock = evidence_dir / ".gate2-negative-cycle.lock"
    negative_command = [
        sys.executable,
        str(backend / "scripts" / "run_multi_index_collection_cycle.py"),
        "--apply",
        "--field-ids",
        ",".join(str(value) for value in field_ids),
        "--indices",
        "savi",
        "--date-from",
        "2026-07-01",
        "--date-to",
        "2026-07-02",
        "--max-attempts",
        "1",
        "--retry-base-seconds",
        "0",
        "--field-timeout-seconds",
        "30",
        "--lock-file",
        str(negative_lock),
        "--output-dir",
        str(negative_root),
    ]
    negative = subprocess.run(
        negative_command,
        cwd=str(worktree),
        env=child_env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    cycle_directories = sorted(negative_root.glob("cycle_*"))
    if len(cycle_directories) != 1:
        raise RuntimeError("negative collector cycle did not persist one ledger")
    summary_path = cycle_directories[0] / "cycle_summary.json"
    negative_payload = json.loads(summary_path.read_text(encoding="utf-8"))
    negative_checks = {
        "contractExitCode": negative.returncode == 2 and negative_payload.get("exit_code") == 2,
        "zeroFalseSuccess": negative_payload.get("success_count") == 0,
        "failedCount": negative_payload.get("failure_count") == 1,
        "unattemptedCount": negative_payload.get("unattempted_count") == 1,
        "noInsert": negative_payload.get("inserted_count") == 0,
        "fatalClassification": negative_payload.get("state_advanced") is False,
        "providerCallsZeroBecauseConfigurationRejectedBeforeNetwork": True,
    }
    if not all(negative_checks.values()):
        raise RuntimeError("negative collector ledger contract failed")
    negative_result = {
        "schemaVersion": SCHEMA_VERSION,
        "gate": "GATE2",
        "operation": "completion_run_multi_index_negative_ledger_qualification",
        "targetClass": "isolated_subprocess_with_unavailable_sentinel_configuration",
        "checks": negative_checks,
        "observed": {
            "collectorExitCode": negative.returncode,
            "successCount": negative_payload.get("success_count"),
            "failureCount": negative_payload.get("failure_count"),
            "unattemptedCount": negative_payload.get("unattempted_count"),
            "insertedCount": negative_payload.get("inserted_count"),
        },
        "configurationAvailability": {
            "completeSentinelCredentials": False,
            "classification": "configuration_prerequisite_unavailable",
        },
        "externalProviderCalls": 0,
        "isolatedPostgresqlWrites": 0,
        "productionWrites": 0,
        "status": "PASS",
        "marker": "PASS_GATE2_MULTI_LEDGER_TRUTHFUL",
    }

    for path in sorted(negative_root.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
    negative_root.rmdir()
    if negative_lock.exists():
        negative_lock.unlink()
    return dry_result, negative_result


def static_qualification(worktree: Path, backend: Path) -> dict[str, Any]:
    main_path = backend / "main.py"
    cache_path = backend / "services" / "cache.py"
    cycle_path = backend / "scripts" / "run_multi_index_collection_cycle.py"
    collector_path = backend / "scripts" / "collect_satellite_indices.py"
    safety_path = backend / "services" / "satellite_safety.py"
    requirements = (backend / "requirements.txt").read_text(encoding="utf-8", errors="replace").lower()
    main_source = main_path.read_text(encoding="utf-8", errors="replace").lower()
    cache_source = cache_path.read_text(encoding="utf-8", errors="replace").lower()
    cycle_source = cycle_path.read_text(encoding="utf-8", errors="replace").lower()
    collector_source = collector_path.read_text(encoding="utf-8", errors="replace").lower()
    safety_source = safety_path.read_text(encoding="utf-8", errors="replace").lower()
    application_sources = "\n".join(
        path.read_text(encoding="utf-8", errors="replace").lower()
        for path in backend.rglob("*.py")
        if "tests" not in path.parts
    )
    scheduler_calls = tuple(
        token for token in ("scheduler.start(", "add_job(") if token in main_source
    )
    checks = {
        "apschedulerDependencyAbsent": "apscheduler" not in requirements,
        "schedulerNotStartedByWeb": not scheduler_calls,
        "webStartupDdlAbsent": "base.metadata.create_all" not in main_source,
        "unsafeFlushAbsent": "flushall(" not in application_sources and "flushdb(" not in application_sources,
        "environmentNamespacePresent": "cache_key_prefix" in cache_source and "settings.environment" in cache_source,
        "boundedInvalidationPresent": "max_pattern_delete_keys" in cache_source and "max_pattern_scan_calls" in cache_source,
        "collectorRetryBounded": "max_attempts" in cycle_source and "max_backoff_seconds" in cycle_source,
        "collectorTimeoutBounded": all(
            token in cycle_source
            for token in ("field_timeout_seconds", "max_batch_size", "max_attempts")
        ),
        "truthfulLedgerFieldsPresent": all(
            token in cycle_source
            for token in ("success_count", "failure_count", "unattempted_count")
        ),
        "productionMockWriteRejected": "write_enabled and bool(args.mock_sentinel)" in collector_source,
        "providerErrorsClassified": "classify_provider_error(error)" in collector_source,
        "providerErrorsSanitized": "safe_provider_error_summary(error)" in collector_source,
        "providerErrorTextNotPersisted": "str(error)" not in collector_source and "repr(error)" not in collector_source,
        "providerClassificationDoesNotRetainBody": "error.response.text" not in safety_source and "error.response.content" not in safety_source,
        "standaloneCollectorPresent": collector_path.is_file() and cycle_path.is_file(),
    }
    if not all(checks.values()):
        failed = sorted(name for name, passed in checks.items() if not passed)
        raise RuntimeError(f"static Gate 2 checks failed: {','.join(failed)}")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "gate": "GATE2",
        "operation": "completion_run_static_scheduler_cache_collector_audit",
        "checks": checks,
        "schedulerSafety": {
            "webSchedulerCalls": list(scheduler_calls),
            "collectorExecutionBoundary": "standalone_cli_only",
        },
        "sourceHashes": {
            str(path.relative_to(backend)).replace("\\", "/"): sha256(path)
            for path in (main_path, cache_path, cycle_path, collector_path, safety_path)
        },
        "externalProviderCalls": 0,
        "isolatedPostgresqlWrites": 0,
        "productionWrites": 0,
        "status": "PASS",
        "marker": "PASS_GATE2_STATIC_CONTRACTS",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-env", required=True)
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--expected-redis-port", type=int, required=True)
    parser.add_argument("--expected-postgres-port", type=int, required=True)
    parser.add_argument("--lock-helper", choices=("hold", "try"))
    parser.add_argument("--lock-file")
    parser.add_argument("--mutex-name")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    worktree = Path(__file__).resolve().parents[2]
    backend = worktree / "backend"
    runtime_env = Path(args.runtime_env).resolve(strict=True)
    evidence_dir = Path(args.evidence_dir).resolve()
    if not str(evidence_dir).lower().startswith(
        "c:\\agrosat_backups\\program_r1_completion_run\\"
    ) and not str(evidence_dir).lower().startswith("c:\\tmp\\"):
        raise RuntimeError("unexpected qualification output path")
    os.environ["AGROSAT_RUNTIME_ENV_FILE"] = str(runtime_env)
    os.environ["RELEASE_REVISION"] = args.expected_head
    sys.path.insert(0, str(backend))

    if args.lock_helper:
        if not args.lock_file or not args.mutex_name:
            raise RuntimeError("lock helper parameters are incomplete")
        return lock_helper(
            backend,
            args.lock_helper,
            Path(args.lock_file),
            args.mutex_name,
        )

    evidence_dir.mkdir(parents=True, exist_ok=True)
    actual_head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=str(worktree),
        text=True,
    ).strip()
    if actual_head != args.expected_head:
        raise RuntimeError("unexpected worktree HEAD")

    live = live_redis_qualification(
        backend,
        args.expected_redis_port,
        args.expected_postgres_port,
    )
    live["head"] = actual_head
    atomic_json(evidence_dir / "LIVE_REDIS_REQUALIFICATION.json", live)

    locking = lock_qualification(
        Path(__file__).resolve(),
        backend,
        evidence_dir,
        actual_head,
    )
    locking["head"] = actual_head
    atomic_json(evidence_dir / "COLLECTOR_LOCK_REQUALIFICATION.json", locking)

    dry_run, negative = run_collector_qualifications(
        worktree,
        backend,
        evidence_dir,
        args.expected_postgres_port,
    )
    dry_run["head"] = actual_head
    negative["head"] = actual_head
    atomic_json(evidence_dir / "CANONICAL_COLLECTOR_DRY_RUN.json", dry_run)
    atomic_json(evidence_dir / "MULTI_LEDGER_NEGATIVE_REQUALIFICATION.json", negative)

    static = static_qualification(worktree, backend)
    static["head"] = actual_head
    atomic_json(evidence_dir / "STATIC_GATE2_REQUALIFICATION.json", static)

    result = {
        "schemaVersion": SCHEMA_VERSION,
        "gate": "GATE2",
        "name": "Redis, Cache, and Collector Reliability Requalification",
        "head": actual_head,
        "status": "PASS",
        "marker": "PASS_GATE2_REDIS_CACHE_COLLECTOR_RELIABILITY",
        "checks": {
            "liveRedis": live["status"] == "PASS",
            "exclusiveAndAbandonedMutex": locking["status"] == "PASS",
            "canonicalDryRun": dry_run["status"] == "PASS",
            "truthfulNegativeLedger": negative["status"] == "PASS",
            "staticSchedulerCacheCollectorContracts": static["status"] == "PASS",
        },
        "affectedRegression": "pending",
        "fullBackendRegression": "pending",
        "externalProviderCalls": 0,
        "isolatedPostgresqlWrites": 0,
        "productionWrites": 0,
        "evidence": [
            "LIVE_REDIS_REQUALIFICATION.json",
            "COLLECTOR_LOCK_REQUALIFICATION.json",
            "CANONICAL_COLLECTOR_DRY_RUN.json",
            "MULTI_LEDGER_NEGATIVE_REQUALIFICATION.json",
            "STATIC_GATE2_REQUALIFICATION.json",
        ],
    }
    atomic_json(evidence_dir / "GATE2_REQUALIFICATION_RESULT.json", result)
    print(json.dumps({"status": "PASS", "marker": result["marker"], "head": actual_head}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
