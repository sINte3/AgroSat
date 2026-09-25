"""Shared, database-free helpers for the TASK_229 collector-state suites.

Not collected by pytest (no ``test_`` prefix). Nothing here imports the code
under test, so the same helpers drive the suites against the base commit and
against the candidate; that is how the discriminating tests are shown to fail
on 7d1a975 for the reason they claim.

* ``ReplaceFaults`` stands in for ``os.replace``, which is what the
  collector's ``atomic_json`` calls to publish a file. It disturbs chosen
  publications of chosen files. On Windows the collisions are real: a
  ``reader`` fault opens the destination exactly as ``services.health`` opens
  a status file, a ``scanner`` fault holds the new temporary file the way an
  antivirus scanner does, and the real ``os.replace`` then fails with the
  operating system's own error. Elsewhere, where replacing an open file
  succeeds, the same faults are simulated with the errors Windows raises.
* ``summary_with_batches()`` builds a completed collector summary with any
  number of field batches, without launching a provider.
* ``fold_by_hand()`` computes schema-2 provider summaries from schema-1 batch
  entries independently of the code under test, and
  ``compact_production_status()`` applies it to TASK_228's production
  fixture.
"""

from __future__ import annotations

from dataclasses import dataclass
import errno
import os
from pathlib import Path
import sys
from typing import Any

from task228_support import production_status


COUNTER_FIELDS = (
    "success_count",
    "failure_count",
    "inserted_count",
    "skipped_existing_count",
    "quality_blocked_count",
    "timeout_count",
)
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
# The byte bound services.health reads a status file with.
STATUS_READ_BOUND = 64 * 1024
REAL_COLLISIONS = sys.platform == "win32"
ERROR_ACCESS_DENIED = 5
ERROR_SHARING_VIOLATION = 32

READER = "reader"  # a reader holds the destination: ERROR_ACCESS_DENIED
SCANNER = "scanner"  # a process holds the new temporary file: ERROR_SHARING_VIOLATION
DISK_FULL = "disk_full"  # not a collision: the device is out of space


def counters(**values: int) -> dict[str, int]:
    result = dict.fromkeys(COUNTER_FIELDS, 0)
    result.update(values)
    return result


def windows_error(code: int, message: str) -> PermissionError:
    """The PermissionError Windows raises, on any platform."""
    error = PermissionError(errno.EACCES, message)
    error.winerror = code
    return error


@dataclass
class Rule:
    """How to disturb the publications of one file.

    ``publications`` are 1-based publication numbers to disturb, or None for
    every publication. ``failures`` is how many attempts of each disturbed
    publication fail; None fails every attempt.
    """

    kind: str = READER
    publications: frozenset[int] | None = None
    failures: int | None = 1
    message: str = "No space left on device"  # for DISK_FULL


class ReplaceFaults:
    """``os.replace`` that disturbs chosen publications of chosen files.

    A retry reuses its temporary file, so each new source path onto a
    destination starts a new publication. ``log`` records every attempt,
    disturbed or not, as ``(file name, publication, outcome)``; ``events``,
    when given, receives the same tuples so a test can order them against
    other calls. ``previous`` holds the bytes the destination had at each
    disturbed attempt.
    """

    def __init__(self, rules: dict[str, Rule] | None = None, events: list | None = None):
        installed = os.replace
        self.real_replace = (
            installed.real_replace if isinstance(installed, ReplaceFaults) else installed
        )
        self.rules = dict(rules or {})
        self.events = events
        self.log: list[tuple[str, int, str]] = []
        self.previous: list[bytes | None] = []
        self._publications: dict[str, int] = {}
        self._current: dict[str, list] = {}

    def install(self, monkeypatch) -> "ReplaceFaults":
        monkeypatch.setattr(os, "replace", self)
        return self

    def attempts(self, name: str) -> list[tuple[int, str]]:
        return [(publication, outcome) for file, publication, outcome in self.log if file == name]

    def _record(self, name: str, publication: int, outcome: str) -> None:
        self.log.append((name, publication, outcome))
        if self.events is not None:
            self.events.append((name, publication, outcome))

    def __call__(self, source, destination, *args, **kwargs):
        name = Path(destination).name
        current = self._current.get(name)
        if current is None or current[0] != os.fspath(source):
            publication = self._publications.get(name, 0) + 1
            self._publications[name] = publication
            current = self._current[name] = [os.fspath(source), publication, 0]
        publication, failed = current[1], current[2]
        rule = self.rules.get(name)
        if (
            rule is None
            or (rule.publications is not None and publication not in rule.publications)
            or (rule.failures is not None and failed >= rule.failures)
        ):
            try:
                result = self.real_replace(source, destination, *args, **kwargs)
            except OSError as error:
                self._record(name, publication, f"real {_outcome(error)}")
                raise
            self._record(name, publication, "replaced")
            return result
        current[2] += 1
        self.previous.append(
            Path(destination).read_bytes() if os.path.isfile(destination) else None
        )
        try:
            self._fail(rule, source, destination)
        except OSError as error:
            self._record(name, publication, _outcome(error))
            raise

    def _fail(self, rule: Rule, source, destination) -> None:
        kind = rule.kind
        if kind == DISK_FULL:
            raise OSError(errno.ENOSPC, rule.message)
        held = destination if kind == READER else source
        if not os.path.isfile(held):
            raise AssertionError(f"no process can hold {Path(held).name}: it does not exist")
        if REAL_COLLISIONS:
            with open(held, "rb") as handle:  # exactly how services.health reads
                handle.read(STATUS_READ_BOUND + 1)
                self.real_replace(source, destination)
            raise AssertionError("replacing a file another handle holds open succeeded")
        if kind == READER:
            raise windows_error(ERROR_ACCESS_DENIED, "Access is denied")
        raise windows_error(
            ERROR_SHARING_VIOLATION,
            "The process cannot access the file because it is being used by another process",
        )


def fold_by_hand(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Schema-2 summaries for schema-1 batch entries, computed independently."""
    folded = []
    for provider in ("ndvi", "multi"):
        own = [entry for entry in entries if entry["provider"] == provider]
        if not own:
            continue
        clean = [entry for entry in own if entry["exit_code"] == 0 and not entry["timed_out"]]
        reported = [entry["counters"] for entry in own if entry.get("counters") is not None]
        folded.append(
            {
                "provider": provider,
                "status": "succeeded"
                if len(clean) == len(own)
                else "failed"
                if not clean
                else "partial",
                "batch_count": len(own),
                "succeeded_batch_count": len(clean),
                "failed_batch_count": len(own) - len(clean),
                "timed_out_batch_count": sum(1 for entry in own if entry["timed_out"]),
                "exit_code": max(entry["exit_code"] for entry in own),
                "timed_out": any(entry["timed_out"] for entry in own),
                "counters": {
                    field: sum(values[field] for values in reported) for field in COUNTER_FIELDS
                }
                if reported
                else None,
                "counters_batch_count": len(reported),
            }
        )
    return folded


def compact_production_status() -> dict[str, Any]:
    """Production's 2026-09-25 01:00 UTC status as schema 2, folded by hand."""
    payload = production_status()
    return {**payload, "schema_version": 2, "providers": fold_by_hand(payload["providers"])}


def _outcome(error: OSError) -> str:
    code = getattr(error, "winerror", None)
    return f"winerror={code}" if code is not None else f"errno={error.errno}"


def inserted_in_batch(number: int, batch_size: int) -> int:
    """Rows batch ``number`` inserts in ``summary_with_batches``: never a
    multiple of the batch count, never more than the batch's fields."""
    return number % (batch_size + 1)


def summary_with_batches(
    batches_per_provider: int,
    *,
    batch_size: int = 25,
    providers: tuple[str, ...] = ("ndvi", "multi"),
    run_id: str = "c" * 32,
) -> dict[str, Any]:
    """A completed collector summary, shaped as ``run()`` records it.

    Every batch succeeded with counters: all its fields succeeded, some
    inserted a new observation (``inserted_in_batch``), the rest already had
    one.
    """
    children = []
    for provider in providers:
        for number in range(1, batches_per_provider + 1):
            inserted = inserted_in_batch(number, batch_size)
            children.append(
                {
                    "provider": provider,
                    "batch": number,
                    "field_count": batch_size,
                    "exit_code": 0,
                    "timed_out": False,
                    "counters": counters(
                        success_count=batch_size,
                        inserted_count=inserted,
                        skipped_existing_count=batch_size - inserted,
                    ),
                    "failure_category": None,
                    "stdout": f"batch {number} finished",
                    "stderr": "",
                }
            )
    return {
        "schema_version": 1,
        "run_id": run_id,
        "mode": "apply",
        "started_at": "2026-09-25T01:00:00+00:00",
        "finished_at": "2026-09-25T04:00:00+00:00",
        "duration_seconds": 10800.0,
        "exit_code": 0,
        "diagnostics": [],
        "scope": {
            "active_field_count": batches_per_provider * batch_size,
            "batch_size": batch_size,
            "batch_count": batches_per_provider,
        },
        "children": children,
    }
