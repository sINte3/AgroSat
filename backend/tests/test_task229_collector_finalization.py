"""TASK_229 / Parts F and G: operational state never rewrites the outcome.

A cycle terminalizes its database run (``satellite_collection_runs``) with
the provider outcome, and publishes the operational files around it. On
7d1a975:

* a reader holding ``collector_latest_status.json`` when the final status was
  published turned a cycle the database had already recorded as succeeded
  into Scheduled Task exit code 4;
* a reader holding ``collector_heartbeat.json`` at ``stop()`` did worse. The
  failure was folded into the exit code *before* ``finish_apply_run``, so the
  database run itself was recorded as failed/operational, and the anomaly and
  verification steps were skipped.

Transient collisions are now ridden out. A permanent inability to persist
operational state is still an operational failure (exit code 4, stated in the
diagnostics and in the run's forensic summary), but it never changes the
database outcome and never leaves the run ``running``.

These tests need no database: its lifecycle is replaced by recorders, as in
TASK_225's contract tests, and ``os.replace`` by ``ReplaceFaults`` (real reader
collisions on Windows). The PostgreSQL proof is
``test_task229_collector_finalization_postgres.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from scripts import collect_satellite as collector
from task229_support import DISK_FULL, READER, SCANNER, ReplaceFaults, Rule


RUN_ID = "9" * 32
LATEST = collector.LATEST_STATUS_FILENAME
SUCCESS = collector.LAST_SUCCESS_FILENAME
HEARTBEAT = collector.HEARTBEAT_FILENAME
SUMMARY = "collector_summary.json"
FINAL = frozenset({2})  # the running snapshot (or first beat) is publication 1
FIRST = frozenset({1})


def apply_invocation(root: Path):
    return collector.parse_args(
        [
            "--apply", "--field-ids", "4,5", "--indices", "ndvi,savi",
            "--output-dir", str(root / "output"),
            "--state-dir", str(root / "state"),
            "--lock-dir", str(root / "locks"),
        ]
    )


def _child(exit_code: int, values: dict[str, int]):
    def child(command, timeout):
        provider_output = Path(command[command.index("--output-dir") + 1])
        (provider_output / "cycle_1").mkdir()
        (provider_output / "cycle_1" / "cycle_summary.json").write_text(
            json.dumps(values), encoding="utf-8"
        )
        return {"exit_code": exit_code, "timed_out": False, "stdout": "ok", "stderr": ""}

    return child


HEALTHY = _child(0, {
    "success_count": 2, "failure_count": 0, "inserted_count": 1,
    "skipped_existing_count": 1, "quality_blocked_count": 0, "timeout_count": 0,
})
LOST_A_FIELD = _child(1, {
    "success_count": 1, "failure_count": 1, "inserted_count": 1,
    "skipped_existing_count": 0, "quality_blocked_count": 0, "timeout_count": 0,
})


def run_apply(root: Path, monkeypatch, *, rules=None, child=HEALTHY, run_id=RUN_ID, delays=None):
    """One apply cycle against recorders for the database lifecycle."""
    from config import settings

    events: list = []
    calls: list = []
    faults = ReplaceFaults(rules, events=events).install(monkeypatch)
    if delays is not None:
        monkeypatch.setattr(collector, "REPLACE_RETRY_DELAYS_SECONDS", delays, raising=False)
    run = SimpleNamespace(session=Mock())

    def record(name, result=None):
        def step(*args, **kwargs):
            calls.append((name, kwargs))
            events.append((name, kwargs.get("exit_code")))
            return result

        return step

    with patch("services.autonomous_monitoring.begin_apply_run", record("begin", run)), \
            patch("services.autonomous_monitoring.heartbeat", record("heartbeat")), \
            patch("services.autonomous_monitoring.refresh_freshness", record("freshness", 0)), \
            patch("services.autonomous_monitoring.reconcile_pixel_candidates", record(
                "promote",
                {"inserted_candidates": 0, "automatic_inspections": 0, "spike_guard_triggered": False},
            )), \
            patch("services.closed_loop_agronomy.reconcile_pending", record("verify", {"eligible": 0})), \
            patch("services.autonomous_monitoring.finish_apply_run", record("finish")), \
            patch.dict("os.environ", {"AGROSAT_RELEASE_COMMIT": "a" * 40}), \
            patch.object(settings, "observation_detection_enabled", False):
        code, summary = collector.run(
            apply_invocation(root),
            child_runner=child,
            lock_acquire=lambda *args, **kwargs: "lock",
            lock_release=lambda lock: None,
            run_id_factory=lambda: run_id,
        )
    return SimpleNamespace(code=code, summary=summary, events=events, calls=calls, faults=faults)


def finishes(result) -> list[dict]:
    return [kwargs for name, kwargs in result.calls if name == "finish"]


def outcomes(result, name: str, publications=None) -> list[str]:
    return [
        outcome
        for publication, outcome in result.faults.attempts(name)
        if publications is None or publication in publications
    ]


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def leftovers(root: Path) -> list[str]:
    return sorted(path.name for path in root.rglob(".tmp-*"))


def diagnostics(result) -> str:
    return "\n".join(result.summary["diagnostics"])


# -- transient collisions: the cycle keeps its outcome ------------------------------


def test_a_reader_at_the_final_status_publication_no_longer_fails_the_cycle(tmp_path, monkeypatch):
    result = run_apply(tmp_path, monkeypatch, rules={LATEST: Rule(READER, publications=FINAL)})

    assert result.code == 0  # 7d1a975: 4, with the database run already succeeded
    [finish] = finishes(result)
    assert (finish["exit_code"], finish["provider_status"], finish["failure_category"]) == (0, "healthy", None)
    assert outcomes(result, LATEST, FINAL) == ["winerror=5", "replaced"]
    latest = read(tmp_path / "output" / LATEST)
    assert (latest["status"], latest["exit_code"]) == ("succeeded", 0)
    assert read(tmp_path / "output" / SUCCESS) == latest
    assert result.summary["diagnostics"] == []
    assert leftovers(tmp_path) == []


def test_a_reader_at_the_final_heartbeat_no_longer_fails_the_cycle(tmp_path, monkeypatch):
    result = run_apply(tmp_path, monkeypatch, rules={HEARTBEAT: Rule(READER, publications=FINAL)})

    assert result.code == 0  # 7d1a975: 4
    [finish] = finishes(result)
    # 7d1a975 recorded this run in the database as exit 4 / degraded / operational.
    assert (finish["exit_code"], finish["provider_status"], finish["failure_category"]) == (0, "healthy", None)
    assert outcomes(result, HEARTBEAT, FINAL) == ["winerror=5", "replaced"]
    assert read(tmp_path / "state" / HEARTBEAT)["run_id"] == RUN_ID
    assert leftovers(tmp_path) == []


def test_collisions_on_every_publication_are_ridden_out(tmp_path, monkeypatch):
    first = run_apply(tmp_path, monkeypatch, run_id="1" * 32)  # every file now exists
    assert first.code == 0

    result = run_apply(
        tmp_path,
        monkeypatch,
        run_id="2" * 32,
        rules={
            LATEST: Rule(READER),  # the running snapshot and the final status
            SUCCESS: Rule(READER),
            HEARTBEAT: Rule(READER),  # the first beat and the final one
            SUMMARY: Rule(SCANNER),  # a scanner holding the new forensic summary
        },
    )

    assert result.code == 0  # 7d1a975: 4, before the cycle even started
    [finish] = finishes(result)
    assert finish["exit_code"] == 0
    for name, publications in ((LATEST, 2), (SUCCESS, 1), (HEARTBEAT, 2), (SUMMARY, 1)):
        attempts = result.faults.attempts(name)
        assert len({publication for publication, _ in attempts}) == publications
        assert [outcome for _, outcome in attempts].count("replaced") == publications
    assert read(tmp_path / "output" / LATEST)["run_id"] == "2" * 32
    assert result.summary["diagnostics"] == []
    assert leftovers(tmp_path) == []


def test_a_partial_cycle_keeps_its_own_outcome_through_a_collision(tmp_path, monkeypatch):
    result = run_apply(
        tmp_path, monkeypatch, child=LOST_A_FIELD, rules={LATEST: Rule(READER, publications=FINAL)}
    )

    assert result.code == 1  # 7d1a975: 4
    [finish] = finishes(result)
    assert (finish["exit_code"], finish["provider_status"]) == (1, "degraded")
    latest = read(tmp_path / "output" / LATEST)
    assert (latest["status"], latest["exit_code"]) == ("failed", 1)


# -- permanent failures: an explicit operational failure, the run still terminal ----


def test_a_permanent_status_failure_fails_the_task_but_not_the_database_run(tmp_path, monkeypatch):
    result = run_apply(
        tmp_path, monkeypatch, rules={LATEST: Rule(DISK_FULL, publications=FINAL, failures=None)}
    )

    assert result.code == 4
    [finish] = finishes(result)  # terminalized exactly once, with the provider outcome
    assert (finish["exit_code"], finish["provider_status"], finish["failure_category"]) == (0, "healthy", None)
    # The run was terminal before the failed publication was even attempted.
    assert result.events.index(("finish", 0)) < result.events.index((LATEST, 2, "errno=28"))
    assert outcomes(result, LATEST, FINAL) == ["errno=28"]  # not a collision: not retried
    assert "latest status persistence failed" in diagnostics(result)
    assert "No space left on device" in diagnostics(result)
    assert leftovers(tmp_path) == []
    # The run's forensic summary records the operational failure as well.
    forensic = read(tmp_path / "output" / f"run_{RUN_ID}" / SUMMARY)
    assert forensic["exit_code"] == 4
    assert any("latest status persistence failed" in item for item in forensic["diagnostics"])


def test_a_collision_outlasting_the_budget_is_a_bounded_operational_failure(tmp_path, monkeypatch):
    delays = (0.001, 0.002, 0.004)
    result = run_apply(
        tmp_path,
        monkeypatch,
        rules={LATEST: Rule(READER, publications=FINAL, failures=None)},
        delays=delays,
    )

    assert result.code == 4
    assert outcomes(result, LATEST, FINAL) == ["winerror=5"] * (len(delays) + 1)
    [finish] = finishes(result)
    assert finish["exit_code"] == 0
    assert "latest status persistence failed" in diagnostics(result)
    # The final status never landed; the running snapshot is what is left.
    assert read(tmp_path / "output" / LATEST)["status"] == "running"
    assert leftovers(tmp_path) == []


def test_a_permanent_heartbeat_failure_does_not_rewrite_the_provider_outcome(tmp_path, monkeypatch):
    result = run_apply(
        tmp_path, monkeypatch, rules={HEARTBEAT: Rule(DISK_FULL, publications=FINAL, failures=None)}
    )

    assert result.code == 4  # an operational failure, stated as one
    [finish] = finishes(result)
    # 7d1a975: (4, "degraded", "operational"), i.e. a failed collection.
    assert (finish["exit_code"], finish["provider_status"], finish["failure_category"]) == (0, "healthy", None)
    steps = [name for name, _ in result.calls]
    assert "promote" in steps and "verify" in steps  # 7d1a975 skipped both
    assert "heartbeat persistence failed" in diagnostics(result)
    latest = read(tmp_path / "output" / LATEST)
    assert (latest["status"], latest["exit_code"], latest["failure_category"]) == ("failed", 4, "operational")
    # ...while every provider batch still reads as the success it was.
    assert [item["exit_code"] for item in latest["providers"]] == [0, 0]
    assert leftovers(tmp_path) == []


def test_a_missed_scheduled_beat_is_reported_without_failing_the_cycle(tmp_path, monkeypatch):
    publishers = []

    class Recorded(collector.HeartbeatPublisher):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            publishers.append(self)

    monkeypatch.setattr(collector, "HeartbeatPublisher", Recorded)

    def child(command, timeout):
        publishers[0]._beat()  # a scheduled beat falls due while this child runs
        return HEALTHY(command, timeout)

    # Heartbeat publications: 1 start, 2 and 3 the beats, 4 stop. Beat 2 fails.
    result = run_apply(
        tmp_path,
        monkeypatch,
        child=child,
        rules={HEARTBEAT: Rule(DISK_FULL, publications=frozenset({2}), failures=None)},
    )

    assert result.code == 0  # the final beat persisted: liveness is current again
    [finish] = finishes(result)
    assert finish["exit_code"] == 0
    assert [outcome for _, outcome in result.faults.attempts(HEARTBEAT)] == [
        "replaced", "errno=28", "replaced", "replaced",
    ]
    assert "heartbeat persistence failed" in diagnostics(result)
    assert "(1 scheduled beat(s) missed)" in diagnostics(result)
    assert leftovers(tmp_path) == []


PUBLICATIONS = {
    "latest_status": (LATEST, FINAL, READER),
    "last_success": (SUCCESS, FIRST, SCANNER),
    "heartbeat": (HEARTBEAT, FINAL, READER),
    "forensic_summary": (SUMMARY, FIRST, SCANNER),
}


@pytest.mark.parametrize("permanent", [False, True], ids=["transient", "permanent"])
@pytest.mark.parametrize("name", sorted(PUBLICATIONS))
def test_the_run_is_terminalized_once_with_its_outcome_whatever_publication_fails(
    tmp_path, monkeypatch, name, permanent
):
    file_name, publications, collision = PUBLICATIONS[name]
    rule = (
        Rule(DISK_FULL, publications=publications, failures=None)
        if permanent
        else Rule(collision, publications=publications)
    )

    result = run_apply(tmp_path, monkeypatch, rules={file_name: rule})

    assert result.code == (4 if permanent else 0)
    [finish] = finishes(result)
    assert (finish["exit_code"], finish["provider_status"]) == (0, "healthy")
    assert ("persistence failed" in diagnostics(result)) is permanent
    assert leftovers(tmp_path) == []


def test_publication_diagnostics_carry_no_paths(tmp_path, monkeypatch):
    """An OSError names both files in full, in repr form (doubled backslashes
    on Windows) that the user-path redaction does not match; the diagnostic
    keeps the error, its code and the file name only."""
    result = run_apply(
        tmp_path,
        monkeypatch,
        rules={LATEST: Rule(READER, publications=FINAL, failures=None)},
        delays=(0.001,),
    )

    assert result.code == 4
    text = diagnostics(result)
    assert "latest status persistence failed: PermissionError [WinError 5]" in text
    for leaked in (str(tmp_path), str(tmp_path).replace("\\", "\\\\"), ".tmp-"):
        assert leaked not in text
    forensic = (tmp_path / "output" / f"run_{RUN_ID}" / SUMMARY).read_text(encoding="utf-8")
    assert "latest status persistence failed" in forensic
    assert ".tmp-" not in forensic


def test_publication_diagnostics_are_sanitized(tmp_path, monkeypatch):
    message = (
        "No space left on device at C:\\Users\\operator\\AppData\\Local\\agrosat "
        "password=hunter2 postgresql://" + "agrosat" + ":" + "pw" + "@db/agrosat"
    )
    result = run_apply(
        tmp_path,
        monkeypatch,
        rules={LATEST: Rule(DISK_FULL, publications=FINAL, failures=None, message=message)},
    )

    assert result.code == 4
    text = json.dumps(result.summary)
    assert "latest status persistence failed" in text
    for leaked in ("hunter2", "operator", "pw@", "postgresql://"):
        assert leaked not in text
    forensic = (tmp_path / "output" / f"run_{RUN_ID}" / SUMMARY).read_text(encoding="utf-8")
    for leaked in ("hunter2", "operator", "pw@", "postgresql://"):
        assert leaked not in forensic
