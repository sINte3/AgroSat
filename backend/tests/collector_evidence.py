"""TASK_251A test support: the completion evidence every real batch runner writes.

``scripts/run_ndvi_collection_cycle.py`` and ``scripts/run_multi_index_collection_cycle.py``
record in ``cycle_summary.json`` exactly which planned fields succeeded, failed or were not
attempted, with matching counts and the runner's exit code. The canonical collector accepts
a batch as complete only with that evidence, so fake batch children in collector tests
write the same contract instead of bare counters.
"""

from __future__ import annotations

import json
from pathlib import Path


def cycle_evidence(command: list[str], exit_code: int, counters: dict[str, int]) -> dict:
    """A runner-shaped summary for the child's own ``--field-ids``.

    The first ``success_count`` planned fields succeeded, the next ``failure_count``
    failed and any remaining ones were not attempted, as a real runner records them.
    """
    ids = [int(item) for item in command[command.index("--field-ids") + 1].split(",")]
    success = counters.get("success_count", 0)
    failure = counters.get("failure_count", 0)
    ok, failed, rest = ids[:success], ids[success:success + failure], ids[success + failure:]
    return {
        "success_count": 0, "failure_count": 0, "inserted_count": 0, "skipped_existing_count": 0,
        "quality_blocked_count": 0, "timeout_count": 0,
        **counters,
        "selected_field_ids": ids, "successful_field_ids": ok, "failed_field_ids": failed,
        "unattempted_field_ids": rest, "unattempted_count": len(rest), "exit_code": exit_code,
    }


def write_cycle_summary(command: list[str], exit_code: int, counters: dict[str, int], *, cycle: str = "cycle_1") -> dict:
    output = Path(command[command.index("--output-dir") + 1])
    (output / cycle).mkdir(exist_ok=True)
    payload = cycle_evidence(command, exit_code, counters)
    (output / cycle / "cycle_summary.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload
