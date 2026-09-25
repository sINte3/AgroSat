"""TASK_228 / Part A: the readiness reader accepts what the collector writes.

The canonical collector (``scripts/collect_satellite.py``) writes one
``providers`` entry per provider path *per field batch*. With 275 active
fields at ``--batch-size 25`` that is 11 NDVI plus 11 multi-index entries.
The reader on 4cd8ea7 rejected any list longer than two, so production's own
successful status file was reported as ``collector.status = "missing"``.

The reader now validates each batch entry strictly and publishes at most one
bounded summary per logical provider. A status file it cannot trust is
reported as ``rejected`` (with a bounded reason), never as healthy and never
as absent.

Status files are persisted here through the collector's own ``atomic_json``,
so the reader sees the exact serialization production sees.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path

import pytest

from scripts import collect_satellite as collector
from services import health
from services.metrics import collector_metrics
from task228_support import COUNTER_FIELDS, counters, production_status


NOW = datetime(2026, 9, 25, 2, 0, tzinfo=timezone.utc)
# Production's configured threshold (config.collector_stale_after_seconds).
STALE_AFTER = 129_600
SUMMARY_KEYS = {
    "provider",
    "status",
    "batch_count",
    "succeeded_batch_count",
    "failed_batch_count",
    "timed_out_batch_count",
    "exit_code",
    "timed_out",
    "counters",
    "counters_batch_count",
}
ABSENT = object()


def batch(provider, *, exit_code=0, timed_out=False, counts=ABSENT):
    entry = {"provider": provider, "exit_code": exit_code, "timed_out": timed_out}
    if counts is not ABSENT:
        entry["counters"] = counts
    return entry


def snapshot(providers, **overrides):
    payload = {
        "schema_version": 1,
        "run_id": "a" * 32,
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


def write_status(directory: Path, payload, label="latest"):
    collector.atomic_json(directory / health.STATUS_FILES[label], payload)


def write_raw(directory: Path, raw: bytes, label="latest"):
    (directory / health.STATUS_FILES[label]).write_bytes(raw)


def component(directory: Path, *, now=NOW, stale_after=STALE_AFTER):
    return health.collector_readiness(str(directory), stale_after, now=now)


def summaries(result):
    return {item["provider"]: item for item in result["latest"]["providers"]}


def assert_rejected(result, reason):
    assert result["status"] == "rejected", result
    assert result["reason"] == reason
    assert result["latest"] is None
    assert result["required_for_api_readiness"] is False


C25 = counters(success_count=25, inserted_count=10, skipped_existing_count=15)


# -- 1. the real production shape -------------------------------------------


def test_production_multibatch_status_is_a_valid_collector_component(tmp_path):
    payload = production_status()
    assert len(payload["providers"]) == 22  # the shape 4cd8ea7 rejects
    write_status(tmp_path, payload)

    result = component(tmp_path)

    assert result["status"] == "succeeded"
    assert result["age_seconds"] == 1938  # 01:27:42Z -> 02:00:00Z
    latest = result["latest"]
    assert latest["run_id"] == payload["run_id"]
    assert latest["exit_code"] == 0 and latest["failure_category"] is None
    assert [item["provider"] for item in latest["providers"]] == ["ndvi", "multi"]
    for item in latest["providers"]:
        own = [e for e in payload["providers"] if e["provider"] == item["provider"]]
        assert set(item) == SUMMARY_KEYS
        assert item["batch_count"] == len(own) == 11
        assert item["status"] == "succeeded"
        assert item["succeeded_batch_count"] == 11
        assert item["failed_batch_count"] == 0
        assert item["timed_out_batch_count"] == 0
        assert item["exit_code"] == 0 and item["timed_out"] is False
        assert item["counters_batch_count"] == 11
        assert item["counters"] == {
            field: sum(entry["counters"][field] for entry in own)
            for field in COUNTER_FIELDS
        }
    ndvi, multi = latest["providers"]
    # The 2026-09-25 cycle: 275 fields per provider path, 146 new NDVI rows.
    assert ndvi["counters"]["success_count"] == 275
    assert ndvi["counters"]["inserted_count"] == 146
    assert multi["counters"]["success_count"] == 275
    assert multi["counters"]["failure_count"] == 0


def test_production_last_success_history_is_published_too(tmp_path):
    payload = production_status()
    write_status(tmp_path, payload)
    write_status(tmp_path, payload, label="last_success")

    result = component(tmp_path)

    assert result["last_success"] is not None
    assert len(result["last_success"]["providers"]) == 2
    assert result["last_success"] == result["latest"]
    assert result["last_failure"] is None


# -- 2. aggregation -----------------------------------------------------------


def test_repeated_batches_aggregate_into_one_summary_per_provider(tmp_path):
    providers = [
        batch("ndvi", counts=counters(success_count=25, inserted_count=10, skipped_existing_count=15)),
        batch("ndvi", counts=counters(success_count=25, inserted_count=5, skipped_existing_count=18, quality_blocked_count=2)),
        batch("ndvi", counts=counters(success_count=7, inserted_count=7)),
        batch("multi", counts=counters(success_count=25, skipped_existing_count=300)),
        batch("multi", counts=counters(success_count=25, skipped_existing_count=280)),
        batch("multi", counts=counters(success_count=7, skipped_existing_count=70)),
    ]
    write_status(tmp_path, snapshot(providers))

    result = component(tmp_path)

    assert result["status"] == "succeeded"
    assert result["latest"]["providers"] == [
        {
            "provider": "ndvi",
            "status": "succeeded",
            "batch_count": 3,
            "succeeded_batch_count": 3,
            "failed_batch_count": 0,
            "timed_out_batch_count": 0,
            "exit_code": 0,
            "timed_out": False,
            "counters": counters(
                success_count=57,
                inserted_count=22,
                skipped_existing_count=33,
                quality_blocked_count=2,
            ),
            "counters_batch_count": 3,
        },
        {
            "provider": "multi",
            "status": "succeeded",
            "batch_count": 3,
            "succeeded_batch_count": 3,
            "failed_batch_count": 0,
            "timed_out_batch_count": 0,
            "exit_code": 0,
            "timed_out": False,
            "counters": counters(success_count=57, skipped_existing_count=650),
            "counters_batch_count": 3,
        },
    ]


def test_aggregation_does_not_depend_on_entry_order(tmp_path):
    ordered = production_status()
    shuffled = dict(ordered)
    shuffled["providers"] = ordered["providers"][::-1]
    write_status(tmp_path, ordered)
    expected = component(tmp_path)["latest"]["providers"]
    write_status(tmp_path, shuffled)

    assert component(tmp_path)["latest"]["providers"] == expected


# -- 3. a failed batch is reported honestly ------------------------------------


def test_one_failed_batch_makes_an_honest_partial_provider(tmp_path):
    providers = [
        batch("ndvi", counts=C25),
        batch("ndvi", exit_code=1, counts=counters(success_count=23, failure_count=2, inserted_count=8, skipped_existing_count=15)),
        batch("ndvi", counts=C25),
        batch("multi", counts=C25),
        batch("multi", counts=C25),
        batch("multi", counts=C25),
    ]
    write_status(
        tmp_path,
        snapshot(providers, status="failed", exit_code=1, failure_category="operational"),
    )

    result = component(tmp_path)

    assert result["status"] == "failed"
    ndvi = summaries(result)["ndvi"]
    assert ndvi["status"] == "partial"
    assert (ndvi["succeeded_batch_count"], ndvi["failed_batch_count"]) == (2, 1)
    assert ndvi["exit_code"] == 1
    assert ndvi["counters"]["failure_count"] == 2
    assert ndvi["counters"]["success_count"] == 73
    assert summaries(result)["multi"]["status"] == "succeeded"


def test_a_fatal_batch_without_counters_is_not_counted_as_zero(tmp_path):
    # Exit 4 stops the cycle: the child left no counters and multi never ran.
    providers = [
        batch("ndvi", counts=C25),
        batch("ndvi", counts=C25),
        batch("ndvi", exit_code=4, counts=None),
    ]
    write_status(
        tmp_path,
        snapshot(providers, status="failed", exit_code=4, failure_category="operational"),
    )

    result = component(tmp_path)

    by_provider = summaries(result)
    assert set(by_provider) == {"ndvi"}
    ndvi = by_provider["ndvi"]
    assert ndvi["status"] == "partial"
    assert ndvi["batch_count"] == 3
    assert ndvi["exit_code"] == 4
    assert ndvi["counters_batch_count"] == 2
    assert ndvi["counters"] == counters(success_count=50, inserted_count=20, skipped_existing_count=30)


def test_a_provider_without_a_clean_batch_is_failed(tmp_path):
    write_status(
        tmp_path,
        snapshot(
            [batch("ndvi", exit_code=2, counts=None)],
            status="failed",
            exit_code=2,
            failure_category="contract",
        ),
    )

    ndvi = summaries(component(tmp_path))["ndvi"]

    assert ndvi["status"] == "failed"
    assert ndvi["counters"] is None
    assert ndvi["counters_batch_count"] == 0


# -- 4. timed-out children ----------------------------------------------------


def test_timed_out_batches_are_represented(tmp_path):
    # A cycle timeout marks every remaining batch timed out with exit code 1.
    providers = [
        batch("ndvi", counts=C25),
        batch("ndvi", exit_code=1, timed_out=True, counts=None),
        batch("ndvi", exit_code=1, timed_out=True, counts=None),
        batch("multi", exit_code=1, timed_out=True, counts=None),
    ]
    write_status(
        tmp_path,
        snapshot(providers, status="failed", exit_code=1, failure_category="network"),
    )

    by_provider = summaries(component(tmp_path))

    ndvi = by_provider["ndvi"]
    assert ndvi["status"] == "partial"
    assert ndvi["timed_out"] is True
    assert ndvi["timed_out_batch_count"] == 2
    assert ndvi["failed_batch_count"] == 2
    assert ndvi["counters"] == C25 and ndvi["counters_batch_count"] == 1
    multi = by_provider["multi"]
    assert multi["status"] == "failed"
    assert multi["timed_out"] is True and multi["timed_out_batch_count"] == 1
    assert multi["counters"] is None


# -- 5. legacy one-entry-per-provider files -----------------------------------


LEGACY_FILES = {
    # TASK_209D, before per-provider counters (b8eef17, f593c88).
    "without_counters": (
        [batch("ndvi"), batch("multi")],
        {"ndvi": None, "multi": None},
    ),
    # TASK_209D counters, before batching (e206973 .. 79ddcaf^).
    "with_counters": (
        [batch("ndvi", counts=C25), batch("multi", counts=None)],
        {"ndvi": C25, "multi": None},
    ),
    # An indices list without multi-index codes.
    "single_provider": (
        [batch("ndvi", counts=C25)],
        {"ndvi": C25},
    ),
}


@pytest.mark.parametrize("name", sorted(LEGACY_FILES))
def test_legacy_one_entry_per_provider_files_remain_accepted(tmp_path, name):
    entries, expected_counters = LEGACY_FILES[name]
    payload = snapshot(entries)
    del payload["schema_version"]  # the pre-TASK_228 test payloads omit it
    write_status(tmp_path, payload)

    result = component(tmp_path)

    assert result["status"] == "succeeded"
    by_provider = summaries(result)
    assert set(by_provider) == set(expected_counters)
    for provider, expected in expected_counters.items():
        item = by_provider[provider]
        assert set(item) == SUMMARY_KEYS
        assert item["batch_count"] == 1
        assert item["status"] == "succeeded"
        assert item["counters"] == expected
        assert item["counters_batch_count"] == (0 if expected is None else 1)


# -- 6/7. malformed providers and counters fail closed -------------------------


MALFORMED_PROVIDERS = {
    "unknown_provider": [batch("sentinel")],
    "provider_not_text": [{"provider": ["ndvi"], "exit_code": 0, "timed_out": False}],
    "provider_missing": [{"exit_code": 0, "timed_out": False}],
    "entry_not_object": ["ndvi"],
    "providers_not_list": {"ndvi": batch("ndvi")},
    "exit_code_text": [batch("ndvi", exit_code="0")],
    "exit_code_boolean": [batch("ndvi", exit_code=False)],
    "exit_code_float": [batch("ndvi", exit_code=0.0)],
    "exit_code_outside_child_contract": [batch("ndvi", exit_code=5)],
    "run_exit_code_on_child": [batch("ndvi", exit_code=130)],
    "exit_code_missing": [{"provider": "ndvi", "timed_out": False}],
    "timed_out_text": [batch("ndvi", timed_out="false")],
    "timed_out_integer": [batch("ndvi", timed_out=0)],
    "timed_out_missing": [{"provider": "ndvi", "exit_code": 0}],
}


@pytest.mark.parametrize("name", sorted(MALFORMED_PROVIDERS))
def test_malformed_provider_entries_fail_closed(tmp_path, name):
    write_status(tmp_path, snapshot(MALFORMED_PROVIDERS[name]))

    assert_rejected(component(tmp_path), "invalid_contract")


def _without(field):
    values = counters(success_count=25)
    del values[field]
    return values


MALFORMED_COUNTERS = {
    "missing_field": _without("timeout_count"),
    "extra_field": {**C25, "attempt_count": 1},
    "negative": counters(success_count=-1),
    "above_bound": counters(success_count=10_000_001),
    "float": counters(success_count=25.0),
    "boolean": counters(success_count=True),
    "text": counters(success_count="25"),
    "null_value": counters(success_count=None),
    "list": [25, 0, 0, 0, 0, 0],
    "text_blob": "success_count=25",
}


@pytest.mark.parametrize("name", sorted(MALFORMED_COUNTERS))
def test_malformed_counters_fail_closed(tmp_path, name):
    providers = [batch("ndvi", counts=C25), batch("ndvi", counts=MALFORMED_COUNTERS[name])]
    write_status(tmp_path, snapshot(providers))

    assert_rejected(component(tmp_path), "invalid_contract")


def test_per_provider_batch_bound_is_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "MAX_BATCHES_PER_PROVIDER", 3)
    write_status(tmp_path, snapshot([batch("ndvi", counts=C25)] * 3 + [batch("multi")] * 3))
    assert component(tmp_path)["status"] == "succeeded"

    write_status(tmp_path, snapshot([batch("ndvi", counts=C25)] * 4))
    assert_rejected(component(tmp_path), "invalid_contract")


def test_batch_bound_is_derived_from_the_collectors_own_limits(tmp_path):
    """The reader's bound is the collector's contract, not a number picked here."""

    def accepts(batch_size):
        args = collector.parse_args(
            [
                "--dry-run",
                "--all-active-fields",
                "--batch-size",
                str(batch_size),
                "--indices",
                "ndvi,savi",
                "--output-dir",
                str(tmp_path / "output"),
                "--state-dir",
                str(tmp_path / "state"),
                "--lock-dir",
                str(tmp_path / "locks"),
            ]
        )
        try:
            collector.validate(args)
        except collector.ContractError:
            return False
        return True

    smallest_batch = next(size for size in range(1, collector.MAX_FIELDS + 1) if accepts(size))
    assert health.MAX_BATCHES_PER_PROVIDER == math.ceil(
        collector.MAX_ACTIVE_FIELDS / smallest_batch
    )
    assert set(health.PROVIDER_COUNTER_FIELDS) == set(collector.PROVIDER_COUNTER_FIELDS)
    assert health.MAX_COUNTER_VALUE == 10_000_000
    assert health.MAX_DURATION_SECONDS == collector.MAX_CYCLE_TIMEOUT_SECONDS


# -- 8. size cap -------------------------------------------------------------


def test_status_file_size_cap_is_enforced_at_the_byte(tmp_path):
    body = json.dumps(snapshot([batch("ndvi", counts=C25), batch("multi", counts=C25)])).encode()
    exact = body + b" " * (health.MAX_STATUS_FILE_BYTES - len(body))
    assert len(exact) == health.MAX_STATUS_FILE_BYTES

    write_raw(tmp_path, exact)
    assert component(tmp_path)["status"] == "succeeded"

    write_raw(tmp_path, exact + b" ")
    assert_rejected(component(tmp_path), "oversized")


def test_oversized_production_like_list_is_rejected_not_truncated(tmp_path):
    payload = production_status()
    payload["providers"] = payload["providers"] * 20  # ~95 KiB serialized
    write_status(tmp_path, payload)

    assert_rejected(component(tmp_path), "oversized")


# -- 9. run id and timestamps ------------------------------------------------


MALFORMED_IDENTITY = {
    "run_id_uppercase": {"run_id": "A" * 32},
    "run_id_short": {"run_id": "a" * 31},
    "run_id_not_text": {"run_id": 12345},
    "run_id_path": {"run_id": "../" + "a" * 29},
    "started_at_missing": {"started_at": None},
    "started_at_naive": {"started_at": "2026-09-25T01:00:00"},
    "started_at_garbage": {"started_at": "yesterday"},
    "started_at_oversized": {"started_at": "2026-09-25T01:00:00+00:00" + " " * 64},
    "started_at_number": {"started_at": 1_758_762_000},
    # Offsets at either end of the calendar overflow when converted to UTC.
    "started_at_before_the_calendar": {"started_at": "0001-01-01T00:00:00+01:00"},
    "finished_at_after_the_calendar": {"finished_at": "9999-12-31T23:59:59-23:59"},
    "finished_at_missing_on_terminal_run": {"finished_at": None},
    "finished_at_naive": {"finished_at": "2026-09-25T01:30:00"},
    "finished_at_garbage": {"finished_at": "later"},
    "finished_at_in_the_future": {"finished_at": "2026-09-26T01:30:00+00:00"},
    "started_at_in_the_future": {"started_at": "2026-09-26T01:00:00+00:00", "finished_at": "2026-09-26T01:30:00+00:00"},
}


@pytest.mark.parametrize("name", sorted(MALFORMED_IDENTITY))
def test_malformed_run_id_and_timestamps_fail_closed(tmp_path, name):
    write_status(tmp_path, snapshot([batch("ndvi", counts=C25)], **MALFORMED_IDENTITY[name]))

    assert_rejected(component(tmp_path), "invalid_contract")


INCONSISTENT_STATES = {
    "succeeded_with_failure_exit": {"status": "succeeded", "exit_code": 1},
    "failed_with_success_exit": {"status": "failed", "exit_code": 0, "failure_category": "operational"},
    "cancelled_without_130": {"status": "cancelled", "exit_code": 1, "failure_category": "operational"},
    "running_with_exit_code": {"status": "running", "exit_code": 0, "finished_at": None},
    "running_with_finished_at": {"status": "running", "exit_code": None},
    "succeeded_with_failure_category": {"status": "succeeded", "exit_code": 0, "failure_category": "network"},
    "unknown_status": {"status": "paused"},
    "status_not_text": {"status": ["succeeded"]},
    "failure_category_not_text": {"status": "failed", "exit_code": 1, "failure_category": ["network"]},
    "failure_category_outside_vocabulary": {"status": "failed", "exit_code": 1, "failure_category": "partial"},
    "exit_code_outside_run_contract": {"status": "failed", "exit_code": 7},
    "duration_boolean": {"duration_seconds": True},
    "duration_negative": {"duration_seconds": -1},
    "duration_above_cycle_cap": {"duration_seconds": 21_601},
    "schema_version_unknown": {"schema_version": 2},
}


@pytest.mark.parametrize("name", sorted(INCONSISTENT_STATES))
def test_self_contradictory_status_fails_closed(tmp_path, name):
    write_status(tmp_path, snapshot([batch("ndvi", counts=C25)], **INCONSISTENT_STATES[name]))

    assert_rejected(component(tmp_path), "invalid_contract")


def test_terminal_states_the_collector_writes_are_accepted(tmp_path):
    cases = (
        ("succeeded", 0, None),
        ("failed", 1, "operational"),
        ("failed", 2, "contract"),
        ("failed", 3, "lock_contention"),
        ("failed", 4, "auth"),
        ("cancelled", 130, "operational"),
    )
    for status, exit_code, category in cases:
        write_status(
            tmp_path,
            snapshot(
                [batch("ndvi", counts=C25)],
                status=status,
                exit_code=exit_code,
                failure_category=category,
            ),
        )
        assert component(tmp_path)["status"] == status


# -- crafted JSON never escapes as a server error ------------------------------


CRAFTED_BYTES = {
    "deeply_nested": b"[" * 30_000 + b"]" * 30_000,
    "huge_integer": b'{"exit_code": ' + b"9" * 5_000 + b"}",
    "not_json": b"collector finished OK",
    "utf8_bom": b"\xef\xbb\xbf" + json.dumps(snapshot([])).encode(),
    "invalid_utf8": b'{"run_id": "\xff\xfe"}',
    "json_array": b"[]",
    "json_null": b"null",
}


@pytest.mark.parametrize("name", sorted(CRAFTED_BYTES))
def test_crafted_status_bytes_are_rejected_without_raising(tmp_path, name):
    write_raw(tmp_path, CRAFTED_BYTES[name])

    result = component(tmp_path)

    assert result["status"] == "rejected"
    assert result["reason"] in {"malformed_json", "invalid_contract"}
    assert result["latest"] is None


def test_non_finite_duration_is_rejected(tmp_path):
    raw = json.dumps(snapshot([batch("ndvi", counts=C25)])).replace(
        '"duration_seconds": 1800.0', '"duration_seconds": NaN'
    )
    assert "NaN" in raw
    write_raw(tmp_path, raw.encode())

    assert_rejected(component(tmp_path), "invalid_contract")


def test_unreadable_status_path_is_rejected(tmp_path):
    (tmp_path / health.STATUS_FILES["latest"]).mkdir()

    assert_rejected(component(tmp_path), "unreadable")


def test_absent_status_is_still_reported_as_missing(tmp_path):
    result = component(tmp_path)

    assert result["status"] == "missing"
    assert result["latest"] is None
    assert "reason" not in result


# -- 10. staleness -------------------------------------------------------------


def test_stale_age_is_measured_from_the_last_transition(tmp_path):
    write_status(tmp_path, snapshot([batch("ndvi", counts=C25)]))
    finished = datetime(2026, 9, 25, 1, 30, tzinfo=timezone.utc)

    fresh = component(tmp_path, now=finished + timedelta(seconds=3600), stale_after=3600)
    stale = component(tmp_path, now=finished + timedelta(seconds=3601), stale_after=3600)

    assert (fresh["status"], fresh["age_seconds"]) == ("succeeded", 3600)
    assert (stale["status"], stale["age_seconds"]) == ("stale", 3601)
    assert stale["latest"]["status"] == "succeeded"


def test_running_age_is_measured_from_the_start(tmp_path):
    write_status(
        tmp_path,
        snapshot(
            [],
            status="running",
            finished_at=None,
            exit_code=None,
            duration_seconds=None,
        ),
    )
    started = datetime(2026, 9, 25, 1, 0, tzinfo=timezone.utc)

    result = component(tmp_path, now=started + timedelta(minutes=10))

    assert (result["status"], result["age_seconds"]) == ("running", 600)


def test_stale_threshold_stays_clamped(tmp_path):
    write_status(tmp_path, snapshot([batch("ndvi", counts=C25)]))

    assert component(tmp_path, stale_after=1)["stale_after_seconds"] == 60
    assert component(tmp_path, stale_after=10**9)["stale_after_seconds"] == 30 * 24 * 3600


# -- the real writer and the reader agree ---------------------------------------


def test_the_collectors_own_multibatch_output_is_accepted(tmp_path, monkeypatch):
    """Run the canonical collector end to end and read back what it wrote."""
    monkeypatch.setattr(
        collector,
        "resolve_active_field_ids",
        lambda: [11, 12, 13, 14, 15, 16, 17],
    )
    seen = {"ndvi": 0, "multi": 0}

    def child(command, timeout):
        joined = " ".join(command)
        provider = "multi" if "run_multi_index_collection_cycle.py" in joined else "ndvi"
        seen[provider] += 1
        # The third multi-index batch loses one field: exit code 1, as the
        # child cycle reports a batch in which some fields failed.
        failing = provider == "multi" and seen[provider] == 3
        fields = len(command[command.index("--field-ids") + 1].split(","))
        output = Path(command[command.index("--output-dir") + 1])
        (output / "cycle_1").mkdir()
        values = counters(
            success_count=fields - int(failing),
            failure_count=int(failing),
            inserted_count=fields - int(failing),
        )
        (output / "cycle_1" / "cycle_summary.json").write_text(
            json.dumps(values), encoding="utf-8"
        )
        return {
            "exit_code": 1 if failing else 0,
            "timed_out": False,
            "stdout": "C:\\Users\\operator\\run",
            "stderr": "",
        }

    args = collector.parse_args(
        [
            "--dry-run",
            "--all-active-fields",
            "--batch-size",
            "2",
            "--indices",
            "ndvi,savi",
            "--output-dir",
            str(tmp_path / "output"),
            "--state-dir",
            str(tmp_path / "state"),
            "--lock-dir",
            str(tmp_path / "locks"),
        ]
    )
    code, summary = collector.run(
        args,
        child_runner=child,
        lock_acquire=lambda *a, **k: "lock",
        lock_release=lambda lock: None,
        run_id_factory=lambda: "b" * 32,
    )
    assert code == 1
    assert len(summary["children"]) == 8  # 4 batches x 2 provider paths

    result = component(tmp_path / "output", now=datetime.now(timezone.utc))

    assert result["status"] == "failed"
    assert result["latest"]["run_id"] == "b" * 32
    assert result["latest"]["failure_category"] == "operational"
    by_provider = summaries(result)
    assert by_provider["ndvi"]["status"] == "succeeded"
    assert by_provider["ndvi"]["batch_count"] == 4
    assert by_provider["ndvi"]["counters"]["success_count"] == 7
    assert by_provider["multi"]["status"] == "partial"
    assert by_provider["multi"]["failed_batch_count"] == 1
    assert by_provider["multi"]["counters"]["failure_count"] == 1
    assert result["last_failure"] == result["latest"]
    assert "Users" not in json.dumps(result)


# -- bounded, sanitized publication ---------------------------------------------


def test_published_component_carries_no_raw_child_detail(tmp_path):
    entry = {
        **batch("ndvi", counts=C25),
        "batch": 1,
        "field_ids": [101, 102],
        "stdout": "Authorization: Bearer abc.def",
        "stderr": "C:\\Users\\operator\\AppData\\secret.txt",
    }
    payload = snapshot([entry], diagnostics=["postgresql://user:pw@db/agrosat"], stdout="x")
    write_status(tmp_path, payload)

    result = component(tmp_path)

    assert result["status"] == "succeeded"
    assert set(result["latest"]) == {
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
    assert set(result["latest"]["providers"][0]) == SUMMARY_KEYS
    serialized = json.dumps(result)
    for leaked in ("field_ids", "stdout", "stderr", "Bearer", "Users", "postgresql", "diagnostics", str(tmp_path)):
        assert leaked not in serialized


def test_collector_state_never_decides_api_readiness(tmp_path):
    write_status(
        tmp_path,
        snapshot(
            [batch("ndvi", exit_code=4, counts=None)],
            status="failed",
            exit_code=4,
            failure_category="operational",
        ),
    )
    for result in (component(tmp_path), component(tmp_path / "absent")):
        assert result["required_for_api_readiness"] is False


def test_metrics_publish_one_series_per_provider_and_outcome(tmp_path):
    write_status(tmp_path, production_status())

    rendered = collector_metrics(component(tmp_path))

    lines = [line for line in rendered.splitlines() if line.startswith("agrosat_collector_last_run_fields")]
    assert len(lines) == len(set(lines)) == 12  # 2 providers x 6 outcomes
    assert 'agrosat_collector_last_run_fields{outcome="inserted_count",provider="ndvi"} 146' in lines
    assert 'agrosat_collector_last_run_fields{outcome="success_count",provider="multi"} 275' in lines
