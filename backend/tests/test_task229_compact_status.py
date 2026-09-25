"""TASK_229 / Parts C, D, E and I: a compact, bounded operational status.

The collector's status files listed every batch child (schema 1): 275 fields
at ``--batch-size 25`` wrote 22 entries, and the files grew with the field
count until the reader's 64 KiB bound, at about 3 800 fields. Every batch
child is already kept in the run's ``collector_summary.json``, so the three
operational files now carry one summary per logical provider (schema 2) and
stay the same size at any field count.

The reader accepts the schema-1 files already on disk (TASK_228's
batch-expanded files and the older one-entry-per-provider ones) and schema 2
alike, and publishes one shape for all of them. The metrics built on it are
covered by test_task229_collector_metrics.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

import pytest

from scripts import collect_satellite as collector
from services import health
from task228_support import production_status
from task229_support import (
    SUMMARY_KEYS,
    compact_production_status,
    counters,
    inserted_in_batch,
    summary_with_batches,
)


NOW = datetime(2026, 9, 25, 5, 0, tzinfo=timezone.utc)
STALE_AFTER = 129_600  # production's collector_stale_after_seconds
LATEST_KEYS = {
    "run_id",
    "mode",
    "status",
    "started_at",
    "finished_at",
    "exit_code",
    "failure_category",
    "duration_seconds",
    "providers",
}
STATUS_KEYS = {"schema_version", *LATEST_KEYS}
C25 = counters(success_count=25, inserted_count=10, skipped_existing_count=15)


def component(directory: Path, *, now=NOW):
    return health.collector_readiness(str(directory), STALE_AFTER, now=now)


def write(directory: Path, payload, label="latest") -> Path:
    path = directory / health.STATUS_FILES[label]
    collector.atomic_json(path, payload)
    return path


def assert_rejected(result, reason="invalid_contract"):
    assert result["status"] == "rejected", result
    assert result["reason"] == reason
    assert result["latest"] is None


def child(provider, *, batch=1, exit_code=0, timed_out=False, counts=None):
    """One batch child as run() records it."""
    return {
        "provider": provider,
        "batch": batch,
        "field_count": 25,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "counters": counts,
        "failure_category": None,
        "stdout": "",
        "stderr": "",
    }


def completed(children, *, exit_code=0, **overrides):
    """A finished run's summary as run() hands it to operational_snapshot."""
    summary = {
        "schema_version": 1,
        "run_id": "e" * 32,
        "mode": "apply",
        "started_at": "2026-09-25T01:00:00+00:00",
        "finished_at": "2026-09-25T01:30:00+00:00",
        "duration_seconds": 1800.0,
        "exit_code": exit_code,
        "diagnostics": [],
        "children": children,
    }
    summary.update(overrides)
    return summary


def schema_1_status(summary):
    """What 7d1a975's collector wrote for this summary: one entry per batch."""
    exit_code = summary["exit_code"]
    return {
        "schema_version": 1,
        "run_id": summary["run_id"],
        "mode": summary["mode"],
        "status": "succeeded" if exit_code == 0 else "cancelled" if exit_code == 130 else "failed",
        "started_at": summary["started_at"],
        "finished_at": summary["finished_at"],
        "duration_seconds": summary["duration_seconds"],
        "exit_code": exit_code,
        "failure_category": collector.classify_failure(summary),
        "providers": [
            {
                "provider": item["provider"],
                "exit_code": item["exit_code"],
                "timed_out": bool(item["timed_out"]),
                "counters": item["counters"],
            }
            for item in summary["children"]
        ],
    }


# -- Part C: the writer ------------------------------------------------------------


def test_the_writer_publishes_one_summary_per_provider():
    summary = summary_with_batches(11)  # production: 275 fields at --batch-size 25

    snapshot = collector.operational_snapshot(summary)

    assert snapshot["schema_version"] == 2
    assert set(snapshot) == STATUS_KEYS
    assert [item["provider"] for item in snapshot["providers"]] == ["ndvi", "multi"]
    for item in snapshot["providers"]:
        assert set(item) == SUMMARY_KEYS
        assert item["status"] == "succeeded"
        assert item["batch_count"] == item["succeeded_batch_count"] == 11
        assert item["failed_batch_count"] == item["timed_out_batch_count"] == 0
        assert item["exit_code"] == 0 and item["timed_out"] is False
        assert item["counters_batch_count"] == 11
        assert item["counters"]["success_count"] == 275
        assert item["counters"]["inserted_count"] == sum(
            inserted_in_batch(number, 25) for number in range(1, 12)
        )


def test_the_running_snapshot_is_compact_too():
    snapshot = collector.operational_snapshot(
        {"run_id": "e" * 32, "mode": "apply", "started_at": "2026-09-25T01:00:00+00:00", "children": []},
        running=True,
    )

    assert snapshot["schema_version"] == 2
    assert snapshot["status"] == "running" and snapshot["exit_code"] is None
    assert snapshot["providers"] == []


MIXES = {
    "all_clean": (
        [child("ndvi", batch=n, counts=C25) for n in (1, 2, 3)]
        + [child("multi", batch=n, counts=C25) for n in (1, 2, 3)],
        0,
    ),
    "one_batch_lost_fields": (
        [
            child("ndvi", batch=1, counts=C25),
            child("ndvi", batch=2, exit_code=1, counts=counters(success_count=23, failure_count=2)),
            child("multi", batch=1, counts=C25),
            child("multi", batch=2, counts=C25),
        ],
        1,
    ),
    "fatal_batch_without_counters": (
        [child("ndvi", batch=1, counts=C25), child("ndvi", batch=2, exit_code=4)],
        4,
    ),
    "cycle_timeout": (
        [
            child("ndvi", batch=1, counts=C25),
            child("ndvi", batch=2, exit_code=1, timed_out=True),
            child("multi", batch=1, exit_code=1, timed_out=True),
        ],
        1,
    ),
    "ndvi_only": ([child("ndvi", batch=n, counts=C25) for n in (1, 2)], 0),
    "no_counters_reported": ([child("ndvi"), child("multi")], 0),
}


@pytest.mark.parametrize("name", sorted(MIXES))
def test_the_compact_status_publishes_exactly_what_the_batch_status_did(tmp_path, name):
    children, exit_code = MIXES[name]
    summary = completed(children, exit_code=exit_code)
    batch_expanded, compact = tmp_path / "schema_1", tmp_path / "schema_2"
    batch_expanded.mkdir()
    compact.mkdir()
    write(batch_expanded, schema_1_status(summary))

    path = write(compact, collector.operational_snapshot(summary))

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["schema_version"] == 2
    assert len(written["providers"]) <= 2
    assert component(compact) == component(batch_expanded)


def test_a_partial_provider_states_its_incomplete_counters(tmp_path):
    lost_two = counters(success_count=23, failure_count=2, inserted_count=3, skipped_existing_count=20)
    summary = completed(
        [
            child("ndvi", batch=1, counts=C25),
            child("ndvi", batch=2, exit_code=1, counts=lost_two),
            child("ndvi", batch=3, exit_code=4),  # crashed: stopped the cycle, left no counters
        ],
        exit_code=4,
    )

    path = write(tmp_path, collector.operational_snapshot(summary))

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["providers"] == [
        {
            "provider": "ndvi",
            "status": "partial",
            "batch_count": 3,
            "succeeded_batch_count": 1,
            "failed_batch_count": 2,
            "timed_out_batch_count": 0,
            "exit_code": 4,
            "timed_out": False,
            # Two of three batches: the third is not counted as zero fields.
            "counters": counters(
                success_count=48, failure_count=2, inserted_count=13, skipped_existing_count=35
            ),
            "counters_batch_count": 2,
        }
    ]
    result = component(tmp_path)
    assert result["status"] == "failed"
    assert result["latest"]["providers"] == written["providers"]


def test_counters_are_never_invented_for_batches_that_left_none():
    summary = completed(
        [child("multi", batch=n, exit_code=1, timed_out=True) for n in (1, 2)], exit_code=1
    )

    [multi] = collector.operational_snapshot(summary)["providers"]

    assert multi["status"] == "failed"
    assert multi["counters"] is None and multi["counters_batch_count"] == 0
    assert multi["timed_out"] is True and multi["timed_out_batch_count"] == 2


# -- Part I: nothing raw, secret or unbounded reaches a status file ------------------


def test_the_status_file_carries_no_raw_child_detail(tmp_path):
    credential_url = "postgresql://" + "agrosat" + ":" + "pw" + "@db:5432/agrosat"
    noisy = {
        **child("ndvi", counts=C25),
        "stdout": f"Authorization: Bearer abc.def {credential_url}",
        "stderr": "C:\\Users\\operator\\AppData\\Local\\token.txt",
        "field_ids": [101, 102],
    }
    summary = completed(
        [noisy],
        diagnostics=[f"DATABASE_URL={credential_url}"],
        monitoring={"verifications": {"eligible": 1}},
        scope={"active_field_count": 25, "batch_size": 25, "batch_count": 1},
    )

    path = write(tmp_path, collector.operational_snapshot(summary))

    raw = path.read_text(encoding="utf-8")
    assert set(json.loads(raw)) == STATUS_KEYS
    for leaked in (
        "stdout", "stderr", "Bearer", "abc.def", "postgresql", "pw@", "Users",
        "DATABASE_URL", "diagnostics", "field_ids", "children", "monitoring",
        "scope", '"batch"', "field_count",
    ):
        assert leaked not in raw


def test_unknown_keys_in_a_compact_file_are_never_published(tmp_path):
    entry = {
        **compact_production_status()["providers"][0],
        "stdout": "Authorization: Bearer abc.def",
        "field_ids": [101, 102],
    }
    payload = {
        **compact_production_status(),
        "providers": [entry],
        "diagnostics": ["postgresql://user:pw@db/agrosat"],
        "hostname": "srv-yoqsh",
    }
    write(tmp_path, payload)

    result = component(tmp_path)

    assert result["status"] == "succeeded"
    assert set(result["latest"]) == LATEST_KEYS
    assert set(result["latest"]["providers"][0]) == SUMMARY_KEYS
    serialized = json.dumps(result)
    for leaked in ("stdout", "Bearer", "field_ids", "diagnostics", "postgresql", "hostname", "srv-yoqsh"):
        assert leaked not in serialized


# -- Part E: size is independent of the field count ----------------------------------


def smallest_accepted_batch(tmp_path: Path) -> int:
    def accepts(batch_size):
        args = collector.parse_args(
            [
                "--dry-run", "--all-active-fields", "--batch-size", str(batch_size),
                "--indices", "ndvi,savi",
                "--output-dir", str(tmp_path / "output"),
                "--state-dir", str(tmp_path / "state"),
                "--lock-dir", str(tmp_path / "locks"),
            ]
        )
        try:
            collector.validate(args)
        except collector.ContractError:
            return False
        return True

    return next(size for size in range(1, collector.MAX_FIELDS + 1) if accepts(size))


def test_status_size_does_not_grow_with_the_batch_count(tmp_path):
    sizes = {}
    for batches in (1, 11, 400, 5_000):
        path = write(tmp_path, collector.operational_snapshot(summary_with_batches(batches)))
        sizes[batches] = path.stat().st_size

    assert max(sizes.values()) <= 2048, sizes
    # Only the digits of the counts grow from 1 to 5 000 batches.
    assert max(sizes.values()) - min(sizes.values()) <= 64, sizes


def test_the_maximum_accepted_scope_keeps_raw_detail_and_bounded_state(tmp_path):
    batch_size = smallest_accepted_batch(tmp_path)
    batches = math.ceil(collector.MAX_ACTIVE_FIELDS / batch_size)  # per provider path
    assert (batch_size, batches) == (2, 5_000)
    summary = summary_with_batches(batches, batch_size=batch_size)

    forensic = tmp_path / "run" / "collector_summary.json"
    collector.atomic_json(forensic, summary)
    raw = json.loads(forensic.read_text(encoding="utf-8"))
    assert len(raw["children"]) == 2 * batches  # every batch child is kept there
    assert raw["children"][-1]["batch"] == batches

    started = time.perf_counter()
    snapshot = collector.operational_snapshot(summary)
    elapsed = time.perf_counter() - started
    path = write(tmp_path, snapshot)

    assert path.stat().st_size <= 2048
    assert [item["provider"] for item in snapshot["providers"]] == ["ndvi", "multi"]
    for item in snapshot["providers"]:
        assert item["batch_count"] == item["counters_batch_count"] == batches
        assert item["counters"]["success_count"] == collector.MAX_ACTIVE_FIELDS
        assert item["counters"]["inserted_count"] == sum(
            inserted_in_batch(number, batch_size) for number in range(1, batches + 1)
        )
        assert item["counters"]["inserted_count"] + item["counters"]["skipped_existing_count"] == (
            collector.MAX_ACTIVE_FIELDS
        )
    assert elapsed < 2.0
    result = component(tmp_path)
    assert result["status"] == "succeeded"
    assert result["latest"]["providers"] == snapshot["providers"]
    # The batch-expanded file for the same run could not have been read at all.
    schema_1 = json.dumps(schema_1_status(summary), sort_keys=True).encode("utf-8")
    assert len(schema_1) > health.MAX_STATUS_FILE_BYTES


# -- Part D: every format on disk reads back in one shape ----------------------------


def test_production_batch_expanded_file_is_still_accepted(tmp_path):
    payload = production_status()
    assert payload["schema_version"] == 1 and len(payload["providers"]) == 22
    write(tmp_path, payload)

    result = component(tmp_path)

    assert result["status"] == "succeeded"
    ndvi, multi = result["latest"]["providers"]
    assert (ndvi["batch_count"], ndvi["succeeded_batch_count"]) == (11, 11)
    assert (ndvi["counters"]["success_count"], ndvi["counters"]["inserted_count"]) == (275, 146)
    assert (multi["batch_count"], multi["counters"]["success_count"]) == (11, 275)


def test_legacy_one_entry_per_provider_file_is_still_accepted(tmp_path):
    payload = {
        "run_id": "a" * 32,
        "mode": "apply",
        "status": "succeeded",
        "started_at": "2026-09-25T01:00:00+00:00",
        "finished_at": "2026-09-25T01:20:00+00:00",
        "duration_seconds": 1200.0,
        "exit_code": 0,
        "failure_category": None,
        "providers": [
            {"provider": "ndvi", "exit_code": 0, "timed_out": False, "counters": C25},
            {"provider": "multi", "exit_code": 0, "timed_out": False},
        ],
    }
    write(tmp_path, payload)

    result = component(tmp_path)

    assert result["status"] == "succeeded"
    ndvi, multi = result["latest"]["providers"]
    assert set(ndvi) == set(multi) == SUMMARY_KEYS
    assert (ndvi["batch_count"], ndvi["counters"], ndvi["counters_batch_count"]) == (1, C25, 1)
    assert (multi["batch_count"], multi["counters"], multi["counters_batch_count"]) == (1, None, 0)


def test_a_compact_file_reads_back_exactly_as_the_batch_expanded_one(tmp_path):
    batch_expanded, compact = tmp_path / "schema_1", tmp_path / "schema_2"
    batch_expanded.mkdir()
    compact.mkdir()
    write(batch_expanded, production_status())
    write(batch_expanded, production_status(), label="last_success")
    write(compact, compact_production_status())
    write(compact, compact_production_status(), label="last_success")

    expected = component(batch_expanded)
    result = component(compact)

    assert result["status"] == "succeeded"
    assert result == expected
    assert json.dumps(result, sort_keys=True) == json.dumps(expected, sort_keys=True)


def test_the_reader_publishes_compact_summaries_in_run_order(tmp_path):
    payload = compact_production_status()
    payload["providers"] = payload["providers"][::-1]
    write(tmp_path, payload)

    result = component(tmp_path)

    assert [item["provider"] for item in result["latest"]["providers"]] == ["ndvi", "multi"]


def entry(provider="ndvi", **overrides):
    """A valid compact summary: 11 clean batches, all with counters."""
    value = {
        "provider": provider,
        "status": "succeeded",
        "batch_count": 11,
        "succeeded_batch_count": 11,
        "failed_batch_count": 0,
        "timed_out_batch_count": 0,
        "exit_code": 0,
        "timed_out": False,
        "counters": counters(success_count=275, inserted_count=146, skipped_existing_count=129),
        "counters_batch_count": 11,
    }
    value.update(overrides)
    return value


def compact(providers, **overrides):
    payload = {
        "schema_version": 2,
        "run_id": "f" * 32,
        "mode": "apply",
        "status": "succeeded",
        "started_at": "2026-09-25T01:00:00+00:00",
        "finished_at": "2026-09-25T01:30:00+00:00",
        "duration_seconds": 1800.0,
        "exit_code": 0,
        "failure_category": None,
        "providers": providers,
    }
    payload.update(overrides)
    return payload


FAILED_RUN = {"status": "failed", "exit_code": 4, "failure_category": "operational"}
VALID_COMPACT = {
    "two_clean_providers": ([entry("ndvi"), entry("multi")], {}),
    "ndvi_only": ([entry("ndvi")], {}),
    "no_providers_yet": ([], {}),
    "partial_with_a_timeout": (
        [
            entry(
                status="partial", succeeded_batch_count=9, failed_batch_count=2,
                timed_out_batch_count=1, exit_code=1, timed_out=True, counters_batch_count=10,
            )
        ],
        FAILED_RUN,
    ),
    "failed_without_counters": (
        [
            entry(
                status="failed", batch_count=1, succeeded_batch_count=0, failed_batch_count=1,
                exit_code=4, counters=None, counters_batch_count=0,
            )
        ],
        FAILED_RUN,
    ),
    # A batch that timed out is failed whatever its exit code.
    "timed_out_batches_that_exited_zero": (
        [
            entry(
                status="failed", batch_count=2, succeeded_batch_count=0, failed_batch_count=2,
                timed_out_batch_count=2, exit_code=0, timed_out=True, counters=None,
                counters_batch_count=0,
            )
        ],
        FAILED_RUN,
    ),
    "totals_at_their_bound": (
        [entry(counters=counters(success_count=11 * 10_000_000))],
        {},
    ),
    "at_the_batch_bound": (
        [entry(batch_count=5_000, succeeded_batch_count=5_000, counters_batch_count=5_000)],
        {},
    ),
}


@pytest.mark.parametrize("name", sorted(VALID_COMPACT))
def test_valid_compact_files_are_accepted_as_written(tmp_path, name):
    providers, overrides = VALID_COMPACT[name]
    write(tmp_path, compact(providers, **overrides))

    result = component(tmp_path)

    assert result["status"] == overrides.get("status", "succeeded")
    assert result["latest"]["providers"] == providers


def _without(key):
    value = entry()
    del value[key]
    return value


def _counters_without(field):
    values = counters(success_count=275)
    del values[field]
    return values


MALFORMED_COMPACT = {
    "providers_not_a_list": {"ndvi": entry()},
    "entry_not_an_object": ["ndvi"],
    "unknown_provider": [entry("sentinel")],
    "provider_not_text": [entry(["ndvi"])],
    "duplicate_provider": [entry("ndvi"), entry("ndvi")],
    "three_entries": [entry("ndvi"), entry("multi"), entry("ndvi")],
    "schema_1_batch_entries": [{"provider": "ndvi", "exit_code": 0, "timed_out": False, "counters": C25}],
    **{f"missing_{key}": [_without(key)] for key in sorted(SUMMARY_KEYS)},
    "no_batches": [
        entry(batch_count=0, succeeded_batch_count=0, counters=None, counters_batch_count=0)
    ],
    "batches_above_the_collectors_bound": [
        entry(batch_count=5_001, succeeded_batch_count=5_001, counters_batch_count=5_001)
    ],
    "batch_count_boolean": [entry(batch_count=True, succeeded_batch_count=1, counters_batch_count=1)],
    "batch_count_float": [entry(batch_count=11.0)],
    "batch_count_text": [entry(batch_count="11")],
    "negative_timed_out_count": [entry(timed_out_batch_count=-1)],
    "more_succeeded_than_batches": [entry(succeeded_batch_count=12)],
    "failed_count_does_not_add_up": [entry(failed_batch_count=1)],
    "more_timed_out_than_failed": [entry(timed_out_batch_count=1, timed_out=True)],
    "status_contradicts_the_counts": [entry(status="partial")],
    "status_outside_vocabulary": [entry(status="degraded")],
    "status_not_text": [entry(status=["succeeded"])],
    "timed_out_flag_contradicts_the_counts": [entry(timed_out=True)],
    "timed_out_flag_integer": [entry(timed_out=0)],
    "exit_code_text": [entry(exit_code="0")],
    "exit_code_boolean": [entry(exit_code=False)],
    "exit_code_float": [entry(exit_code=0.0)],
    "exit_code_of_a_whole_run": [entry(exit_code=130)],
    "exit_code_outside_child_contract": [entry(exit_code=5)],
    "nonzero_exit_with_every_batch_clean": [entry(exit_code=1)],
    "zero_exit_with_a_failed_batch": [
        entry(status="partial", succeeded_batch_count=9, failed_batch_count=2, exit_code=0)
    ],
    "more_counted_than_batches": [entry(counters_batch_count=12)],
    "coverage_without_counters": [entry(counters=None)],
    "counters_without_coverage": [entry(counters_batch_count=0)],
    "counters_missing_a_field": [entry(counters=_counters_without("timeout_count"))],
    "counters_extra_field": [entry(counters={**counters(success_count=275), "attempt_count": 1})],
    "counters_negative": [entry(counters=counters(success_count=-1))],
    "totals_above_their_bound": [entry(counters=counters(success_count=11 * 10_000_000 + 1))],
    "counters_boolean": [entry(counters=counters(success_count=True))],
    "counters_float": [entry(counters=counters(success_count=275.0))],
    "counters_text": [entry(counters=counters(success_count="275"))],
    "counters_list": [entry(counters=[275, 0, 0, 0, 0, 0])],
}


@pytest.mark.parametrize("name", sorted(MALFORMED_COMPACT))
def test_malformed_compact_files_fail_closed(tmp_path, name):
    write(tmp_path, compact(MALFORMED_COMPACT[name]))

    assert_rejected(component(tmp_path))


def test_the_compact_batch_bound_is_the_readers_own(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "MAX_BATCHES_PER_PROVIDER", 3)
    write(tmp_path, compact([entry(batch_count=3, succeeded_batch_count=3, counters_batch_count=3)]))
    assert component(tmp_path)["status"] == "succeeded"

    write(tmp_path, compact([entry(batch_count=4, succeeded_batch_count=4, counters_batch_count=4)]))
    assert_rejected(component(tmp_path))


@pytest.mark.parametrize("version", [0, 3, 99, "2", 2.0, True, None, [2]])
def test_unknown_schema_versions_fail_closed(tmp_path, version):
    write(tmp_path, compact([entry()], schema_version=version))

    assert_rejected(component(tmp_path))


CONTRADICTORY_RUNS = {
    "succeeded_with_failure_exit": {"status": "succeeded", "exit_code": 1},
    "running_with_finish_time": {"status": "running", "exit_code": None},
    "failure_category_outside_vocabulary": {**FAILED_RUN, "failure_category": "partial"},
    "run_id_not_hex": {"run_id": "Z" * 32},
    "finished_in_the_future": {"finished_at": "2026-09-26T01:30:00+00:00"},
    "duration_above_the_cycle_cap": {"duration_seconds": 21_601},
}


@pytest.mark.parametrize("name", sorted(CONTRADICTORY_RUNS))
def test_compact_files_keep_the_run_level_contract(tmp_path, name):
    write(tmp_path, compact([entry()], **CONTRADICTORY_RUNS[name]))

    assert_rejected(component(tmp_path))


def test_an_oversized_compact_file_is_still_rejected_by_size(tmp_path):
    raw = json.dumps(compact([entry()])).encode("utf-8")
    (tmp_path / health.STATUS_FILES["latest"]).write_bytes(
        raw + b" " * (health.MAX_STATUS_FILE_BYTES + 1 - len(raw))
    )

    assert_rejected(component(tmp_path), "oversized")


# -- Acceptance G: a real-shaped cycle, written and read back -----------------------


def test_a_real_shaped_cycle_reads_back_as_succeeded(tmp_path, monkeypatch):
    monkeypatch.setattr(collector, "resolve_active_field_ids", lambda: list(range(1001, 1276)))

    def provider_child(command, timeout):
        fields = len(command[command.index("--field-ids") + 1].split(","))
        output = Path(command[command.index("--output-dir") + 1])
        (output / "cycle_1").mkdir()
        (output / "cycle_1" / "cycle_summary.json").write_text(
            json.dumps(counters(success_count=fields, inserted_count=fields // 2,
                                skipped_existing_count=fields - fields // 2)),
            encoding="utf-8",
        )
        return {"exit_code": 0, "timed_out": False, "stdout": "ok", "stderr": ""}

    output = tmp_path / "output"
    args = collector.parse_args(
        [
            "--dry-run", "--all-active-fields", "--batch-size", "25",
            "--indices", "ndvi,savi,evi,ndmi,ndre",
            "--output-dir", str(output),
            "--state-dir", str(tmp_path / "state"),
            "--lock-dir", str(tmp_path / "locks"),
        ]
    )
    code, summary = collector.run(
        args,
        child_runner=provider_child,
        lock_acquire=lambda *a, **k: "lock",
        lock_release=lambda lock: None,
        run_id_factory=lambda: "b" * 32,
    )

    assert code == 0 and len(summary["children"]) == 22
    latest_path = output / collector.LATEST_STATUS_FILENAME
    written = json.loads(latest_path.read_text(encoding="utf-8"))
    assert written["schema_version"] == 2 and len(written["providers"]) == 2
    # Production's batch-expanded file for the same cycle was 4 971 bytes.
    assert latest_path.stat().st_size < 2048
    result = component(output, now=datetime.now(timezone.utc))
    assert result["status"] == "succeeded"
    ndvi, multi = result["latest"]["providers"]
    for item in (ndvi, multi):
        assert (item["batch_count"], item["succeeded_batch_count"], item["counters_batch_count"]) == (11, 11, 11)
        assert item["counters"]["success_count"] == 275
    assert result["last_success"] == result["latest"]
    assert result["last_failure"] is None
    forensic = json.loads(
        (output / f"run_{'b' * 32}" / "collector_summary.json").read_text(encoding="utf-8")
    )
    assert len(forensic["children"]) == 22  # the raw batch detail stays here
