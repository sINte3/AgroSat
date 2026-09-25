"""TASK_229 / Part H: metrics never present partial counters as complete.

``/api/operations/metrics`` published each provider's summed field counters
even when ``counters_batch_count < batch_count`` (TASK_228 residual risk 2): a
sum over some batches read as the provider's field total. Field series are
now published only when every batch reported counters, and one
low-cardinality gauge per logical provider says whether they did:

    agrosat_collector_last_run_provider_counters_complete{provider="ndvi"} 1

The collector status is read through the real reader
(``services.health.collector_readiness``), from schema-1 and schema-2 files.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re

from scripts import collect_satellite as collector
from services import health
from services.metrics import collector_metrics
from task228_support import production_status
from task229_support import compact_production_status, counters


NOW = datetime(2026, 9, 25, 5, 0, tzinfo=timezone.utc)
STALE_AFTER = 129_600  # production's collector_stale_after_seconds
COMPLETE = "agrosat_collector_last_run_provider_counters_complete"
FIELDS = "agrosat_collector_last_run_fields"
C25 = counters(success_count=25, inserted_count=10, skipped_existing_count=15)


def component(directory: Path):
    return health.collector_readiness(str(directory), STALE_AFTER, now=NOW)


def write(directory: Path, payload) -> None:
    collector.atomic_json(directory / health.STATUS_FILES["latest"], payload)


def rendered(result):
    return collector_metrics(result).splitlines()


def test_complete_counters_are_published_and_flagged_complete(tmp_path):
    write(tmp_path, production_status())

    lines = rendered(component(tmp_path))

    assert f'{COMPLETE}{{provider="ndvi"}} 1' in lines
    assert f'{COMPLETE}{{provider="multi"}} 1' in lines
    fields = [line for line in lines if line.startswith(FIELDS)]
    assert len(fields) == len(set(fields)) == 12
    assert f'{FIELDS}{{outcome="inserted_count",provider="ndvi"}} 146' in fields


def test_partial_counters_are_not_published_as_field_totals(tmp_path):
    """TASK_228 residual risk 2: a partial sum read as the provider's total."""
    payload = {
        **production_status(),
        "status": "failed",
        "exit_code": 4,
        "failure_category": "operational",
        "providers": [
            {"provider": "ndvi", "exit_code": 0, "timed_out": False, "counters": C25},
            {"provider": "ndvi", "exit_code": 4, "timed_out": False, "counters": None},
            {"provider": "multi", "exit_code": 0, "timed_out": False, "counters": C25},
        ],
    }
    write(tmp_path, payload)
    result = component(tmp_path)
    ndvi = result["latest"]["providers"][0]
    assert (ndvi["counters_batch_count"], ndvi["batch_count"]) == (1, 2)  # readiness states the gap

    lines = rendered(result)

    assert f'{COMPLETE}{{provider="ndvi"}} 0' in lines
    assert f'{COMPLETE}{{provider="multi"}} 1' in lines
    assert [line for line in lines if line.startswith(FIELDS) and 'provider="ndvi"' in line] == []
    assert len([line for line in lines if line.startswith(FIELDS) and 'provider="multi"' in line]) == 6


def test_a_provider_without_counters_is_flagged_incomplete(tmp_path):
    payload = {
        **production_status(),
        "status": "failed",
        "exit_code": 2,
        "failure_category": "contract",
        "providers": [{"provider": "ndvi", "exit_code": 2, "timed_out": False, "counters": None}],
    }
    write(tmp_path, payload)

    lines = rendered(component(tmp_path))

    assert f'{COMPLETE}{{provider="ndvi"}} 0' in lines
    assert [line for line in lines if line.startswith(FIELDS)] == []


def test_two_providers_never_duplicate_a_series(tmp_path):
    write(tmp_path, production_status())
    result = component(tmp_path)
    lines = rendered(result)
    assert len(lines) == len(set(lines))
    assert sorted(line for line in lines if line.startswith(COMPLETE)) == [
        f'{COMPLETE}{{provider="multi"}} 1',
        f'{COMPLETE}{{provider="ndvi"}} 1',
    ]

    # Even a component that lists a provider twice renders one series each.
    doubled = {**result, "latest": {**result["latest"], "providers": [
        result["latest"]["providers"][0], *result["latest"]["providers"],
    ]}}
    lines = rendered(doubled)

    assert len(lines) == len(set(lines))
    assert len([line for line in lines if line.startswith(COMPLETE)]) == 2
    series = [line.rsplit(" ", 1)[0] for line in lines]
    assert len(series) == len(set(series))


def test_metrics_are_the_same_for_both_schemas(tmp_path):
    batch_expanded, compact = tmp_path / "schema_1", tmp_path / "schema_2"
    batch_expanded.mkdir()
    compact.mkdir()
    write(batch_expanded, production_status())
    write(compact, compact_production_status())

    assert collector_metrics(component(compact)) == collector_metrics(component(batch_expanded))


def test_collector_metric_labels_stay_low_cardinality(tmp_path):
    write(tmp_path, production_status())

    lines = rendered(component(tmp_path))

    assert lines
    for line in lines:
        assert set(re.findall(r'(\w+)="', line)) <= {"provider", "outcome", "status", "failure_category"}
    joined = "\n".join(lines)
    assert "run_id" not in joined and "field_id" not in joined
    assert production_status()["run_id"] not in joined
