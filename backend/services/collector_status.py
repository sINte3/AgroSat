"""The collector's operational status contract, shared by writer and reader.

``scripts/collect_satellite.py`` publishes the state of its latest run in
small operational files, and ``services/health.py`` reads them back for
readiness and metrics. A run executes each provider path as a series of field
batches. This module is the one definition of a provider batch and of how a
provider's batches fold into its published summary, so the summaries the
writer publishes and the summaries the reader derives from older files cannot
drift apart.

It is pure: no settings, database or filesystem access, so the standalone
collector can import it.
"""

from __future__ import annotations

from typing import Any, Iterable


# The operational status schema the collector writes: at most one summary per
# logical provider, so the files stay the same size at any field count. In
# schema 1 the collector listed every batch child, one entry per provider path
# per field batch; readers still accept those files.
STATUS_SCHEMA_VERSION = 2
# The collector's provider paths, in the order it runs them.
COLLECTOR_PROVIDERS = ("ndvi", "multi")
# Exit codes of one provider child, i.e. one field batch. Only the run as a
# whole can also end with 130 (cancelled).
CHILD_EXIT_CODES = (0, 1, 2, 3, 4)
PROVIDER_COUNTER_FIELDS = (
    "success_count",
    "failure_count",
    "inserted_count",
    "skipped_existing_count",
    "quality_blocked_count",
    "timeout_count",
)
# The bound on one batch's counters. A provider total can reach this times the
# number of batches it covers.
MAX_COUNTER_VALUE = 10_000_000


def valid_counters(value: Any, limit: int = MAX_COUNTER_VALUE) -> bool:
    """Exactly the counter fields, each an integer from 0 to ``limit``."""
    return (
        isinstance(value, dict)
        and set(value) == set(PROVIDER_COUNTER_FIELDS)
        and all(type(item) is int and 0 <= item <= limit for item in value.values())
    )


def batch_record(entry: Any) -> dict[str, Any] | None:
    """One provider batch as the collector records it, or None."""
    if not isinstance(entry, dict):
        return None
    provider = entry.get("provider")
    exit_code = entry.get("exit_code")
    timed_out = entry.get("timed_out")
    # Absent in files written before the collector reported counters.
    counters = entry.get("counters")
    if (
        not isinstance(provider, str)
        or provider not in COLLECTOR_PROVIDERS
        or type(exit_code) is not int
        or exit_code not in CHILD_EXIT_CODES
        or type(timed_out) is not bool
        or (counters is not None and not valid_counters(counters))
    ):
        return None
    return {
        "provider": provider,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "counters": counters,
    }


def provider_summary(
    provider: str,
    *,
    batch_count: int,
    succeeded_batch_count: int,
    timed_out_batch_count: int,
    exit_code: int,
    counters: dict[str, int] | None,
    counters_batch_count: int,
) -> dict[str, Any]:
    """The published summary of one provider path, with its derived fields."""
    return {
        "provider": provider,
        "status": "succeeded"
        if succeeded_batch_count == batch_count
        else "failed"
        if succeeded_batch_count == 0
        else "partial",
        "batch_count": batch_count,
        "succeeded_batch_count": succeeded_batch_count,
        "failed_batch_count": batch_count - succeeded_batch_count,
        "timed_out_batch_count": timed_out_batch_count,
        "exit_code": exit_code,
        "timed_out": timed_out_batch_count > 0,
        "counters": None
        if counters is None
        else {field: counters[field] for field in sorted(PROVIDER_COUNTER_FIELDS)},
        "counters_batch_count": counters_batch_count,
    }


def summarize_provider_batches(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold batch records into at most one summary per provider path.

    ``records`` are ``batch_record`` results. A batch succeeded when its child
    exited 0 without timing out. Every other batch failed: exit 1 means some of
    its fields failed, 2-4 that the child itself did, and a timed-out batch is
    counted as timed out as well. A provider is ``succeeded`` when all its
    batches succeeded, ``failed`` when none did, and ``partial`` otherwise.
    ``exit_code`` is the highest batch exit code, which for anything the
    collector writes is how it folds its own run: a stopping code 2-4, else 1
    if any batch lost fields, else 0. Counters are summed only over the
    batches that reported them, and ``counters_batch_count`` says how many
    did: a batch that left no counters is never counted as zero fields.
    Summaries come in run order; a provider path with no batch is absent.
    """
    batches: dict[str, list[dict[str, Any]]] = {
        provider: [] for provider in COLLECTOR_PROVIDERS
    }
    for record in records:
        batches[record["provider"]].append(record)
    summaries = []
    for provider, own in batches.items():
        if not own:
            continue
        reported = [item["counters"] for item in own if item["counters"] is not None]
        summaries.append(
            provider_summary(
                provider,
                batch_count=len(own),
                succeeded_batch_count=sum(
                    1 for item in own if item["exit_code"] == 0 and not item["timed_out"]
                ),
                timed_out_batch_count=sum(1 for item in own if item["timed_out"]),
                exit_code=max(item["exit_code"] for item in own),
                counters={
                    field: sum(values[field] for values in reported)
                    for field in PROVIDER_COUNTER_FIELDS
                }
                if reported
                else None,
                counters_batch_count=len(reported),
            )
        )
    return summaries
