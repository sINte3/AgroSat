"""TASK_229 / Parts A, B and F: publishing a status file survives its reader.

On Windows, Python opens files without ``FILE_SHARE_DELETE``. While
``services.health`` reads a collector status file, ``os.replace`` onto that
file fails with ``PermissionError [WinError 5]``; a process holding the new
temporary file fails it with ``[WinError 32]``. 7d1a975's ``atomic_json``
raised at the first such collision, so a health probe at the wrong moment
turned a successful collection cycle into exit code 4.

The final replacement is now retried on a bounded, deterministic schedule for
exactly those collisions, and for nothing else. Everything before it (a
temporary file in the same directory, sanitized UTF-8 JSON, flush, fsync) is
unchanged, and the destination is never written in place.

On Windows the collisions below are real (``task229_support.ReplaceFaults``
opens the file as the reader does); elsewhere they are simulated with the
errors Windows raises, so the contract is exercised on every platform.
"""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import stat

import pytest

from scripts import collect_satellite as collector
from task229_support import (
    DISK_FULL,
    ERROR_ACCESS_DENIED,
    ERROR_SHARING_VIOLATION,
    READER,
    REAL_COLLISIONS,
    SCANNER,
    ReplaceFaults,
    Rule,
    windows_error,
)


windows_only = pytest.mark.skipif(
    not REAL_COLLISIONS, reason="needs the Windows file-sharing semantics"
)
PREVIOUS = {"schema_version": 2, "run_id": "a" * 32, "status": "running"}
# sanitize() bounds every string to 4 000 characters; stay under it.
CURRENT = {"schema_version": 2, "run_id": "a" * 32, "status": "succeeded", "padding": "x" * 2000}


def publish_previous(path: Path) -> bytes:
    raw = json.dumps(PREVIOUS, sort_keys=True).encode("utf-8")
    path.write_bytes(raw)
    return raw


def leftovers(directory: Path) -> list[str]:
    return sorted(item.name for item in directory.iterdir() if item.name.startswith(".tmp-"))


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class Sleeps(list):
    """An injected sleep: records each delay instead of waiting."""

    def __call__(self, seconds: float) -> None:
        self.append(seconds)


# -- the defect, reproduced on this host ------------------------------------------


@windows_only
def test_this_host_refuses_to_replace_a_file_a_reader_holds(tmp_path):
    """The operating-system fact the fix rests on, as TASK_228 recorded it."""
    path = tmp_path / collector.LATEST_STATUS_FILENAME
    publish_previous(path)
    incoming = tmp_path / "incoming.json"
    incoming.write_text("{}", encoding="utf-8")

    with open(path, "rb") as reader:  # how services.health reads a status file
        reader.read(64 * 1024 + 1)
        with pytest.raises(PermissionError) as raised:
            os.replace(incoming, path)
    assert raised.value.winerror == ERROR_ACCESS_DENIED

    os.replace(incoming, path)  # the reader is gone: the same call succeeds
    assert read(path) == {}


@windows_only
def test_a_reader_holding_the_status_file_no_longer_fails_the_publication(
    tmp_path, monkeypatch
):
    """Part B: the real collision, ridden out within the bound."""
    path = tmp_path / collector.LATEST_STATUS_FILENAME
    publish_previous(path)
    faults = ReplaceFaults({path.name: Rule(READER, failures=1)}).install(monkeypatch)

    collector.atomic_json(path, CURRENT)

    assert faults.attempts(path.name) == [(1, "winerror=5"), (1, "replaced")]
    assert read(path) == CURRENT
    assert leftovers(tmp_path) == []


@windows_only
def test_a_reader_across_several_attempts_is_waited_out(tmp_path, monkeypatch):
    path = tmp_path / collector.LATEST_STATUS_FILENAME
    previous = publish_previous(path)
    faults = ReplaceFaults({path.name: Rule(READER, failures=3)}).install(monkeypatch)

    collector.atomic_json(path, CURRENT)

    outcomes = [outcome for _, outcome in faults.attempts(path.name)]
    assert outcomes == ["winerror=5"] * 3 + ["replaced"]
    # Until the one successful replacement the destination was the previous
    # complete document, never a truncated or partial one.
    assert faults.previous == [previous] * 3
    assert read(path) == CURRENT
    assert leftovers(tmp_path) == []


@windows_only
def test_a_scanner_holding_the_new_file_is_waited_out(tmp_path, monkeypatch):
    path = tmp_path / collector.LATEST_STATUS_FILENAME
    publish_previous(path)
    faults = ReplaceFaults({path.name: Rule(SCANNER, failures=2)}).install(monkeypatch)

    collector.atomic_json(path, CURRENT)

    outcomes = [outcome for _, outcome in faults.attempts(path.name)]
    assert outcomes == ["winerror=32", "winerror=32", "replaced"]
    assert read(path) == CURRENT
    assert leftovers(tmp_path) == []


@windows_only
def test_a_reader_that_never_lets_go_is_a_bounded_failure(tmp_path, monkeypatch):
    path = tmp_path / collector.LATEST_STATUS_FILENAME
    previous = publish_previous(path)
    faults = ReplaceFaults({path.name: Rule(READER, failures=None)}).install(monkeypatch)
    sleeps = Sleeps()

    with pytest.raises(PermissionError) as raised:
        collector.atomic_json(path, CURRENT, sleep=sleeps)

    assert raised.value.winerror == ERROR_ACCESS_DENIED
    assert len(faults.attempts(path.name)) == len(collector.REPLACE_RETRY_DELAYS_SECONDS) + 1
    assert sleeps == list(collector.REPLACE_RETRY_DELAYS_SECONDS)
    assert path.read_bytes() == previous
    assert leftovers(tmp_path) == []


@windows_only
def test_a_read_only_destination_is_not_mistaken_for_a_reader(tmp_path, monkeypatch):
    """ERROR_ACCESS_DENIED from a read-only file is permanent: no retry."""
    path = tmp_path / collector.LATEST_STATUS_FILENAME
    previous = publish_previous(path)
    os.chmod(path, stat.S_IREAD)
    faults = ReplaceFaults().install(monkeypatch)
    try:
        with pytest.raises(PermissionError) as raised:
            collector.atomic_json(path, CURRENT)
        assert raised.value.winerror == ERROR_ACCESS_DENIED
        assert faults.attempts(path.name) == [(1, "real winerror=5")]
        assert path.read_bytes() == previous
        assert leftovers(tmp_path) == []
    finally:
        os.chmod(path, stat.S_IWRITE)


def test_a_directory_in_the_way_is_not_mistaken_for_a_reader(tmp_path, monkeypatch):
    """Windows reports a directory destination as ERROR_ACCESS_DENIED too."""
    path = tmp_path / collector.LATEST_STATUS_FILENAME
    path.mkdir()
    faults = ReplaceFaults().install(monkeypatch)

    with pytest.raises(OSError):
        collector.atomic_json(path, CURRENT)

    assert len(faults.attempts(path.name)) == 1
    assert path.is_dir()
    assert leftovers(tmp_path) == []


# -- the retry contract, with injected failures and time ---------------------------


@pytest.mark.parametrize("kind", [READER, SCANNER])
@pytest.mark.parametrize("failures", [1, 3, "budget"])
def test_transient_collisions_are_retried_until_one_replacement_succeeds(
    tmp_path, monkeypatch, kind, failures
):
    delays = collector.REPLACE_RETRY_DELAYS_SECONDS
    failures = len(delays) if failures == "budget" else failures
    path = tmp_path / collector.LATEST_STATUS_FILENAME
    previous = publish_previous(path)
    faults = ReplaceFaults({path.name: Rule(kind, failures=failures)}).install(monkeypatch)
    sleeps = Sleeps()

    collector.atomic_json(path, CURRENT, sleep=sleeps)

    attempts = faults.attempts(path.name)
    assert [outcome for _, outcome in attempts].count("replaced") == 1
    assert attempts[-1][1] == "replaced"
    assert len(attempts) == failures + 1
    # One publication: every retry reused the same temporary file.
    assert {publication for publication, _ in attempts} == {1}
    assert sleeps == list(delays[:failures])
    assert faults.previous == [previous] * failures
    assert read(path) == CURRENT
    assert leftovers(tmp_path) == []


def test_collisions_beyond_the_budget_fail_bounded_and_clean_up(tmp_path, monkeypatch):
    path = tmp_path / collector.LATEST_STATUS_FILENAME
    previous = publish_previous(path)
    faults = ReplaceFaults({path.name: Rule(READER, failures=None)}).install(monkeypatch)
    sleeps = Sleeps()

    with pytest.raises(PermissionError) as raised:
        collector.atomic_json(path, CURRENT, sleep=sleeps)

    assert getattr(raised.value, "winerror", None) == ERROR_ACCESS_DENIED
    assert len(faults.attempts(path.name)) == len(collector.REPLACE_RETRY_DELAYS_SECONDS) + 1
    assert sleeps == list(collector.REPLACE_RETRY_DELAYS_SECONDS)
    assert path.read_bytes() == previous
    assert leftovers(tmp_path) == []


def test_the_backoff_schedule_is_deterministic_and_bounded():
    delays = collector.REPLACE_RETRY_DELAYS_SECONDS
    assert isinstance(delays, tuple) and len(delays) >= 3
    assert all(type(delay) is float and delay > 0 for delay in delays)
    assert list(delays) == sorted(delays)
    # The whole budget stays under a second: a reader holds a status file for
    # one bounded read, and a genuine failure must not stall finalization.
    assert sum(delays) < 1.0


def _raising(error: OSError):
    calls = []

    def replace(source, destination, *args, **kwargs):
        calls.append(os.fspath(source))
        raise error

    return replace, calls


UNRELATED_ERRORS = {
    "disk_full": lambda: OSError(errno.ENOSPC, "No space left on device"),
    "io_error": lambda: OSError(errno.EIO, "Input/output error"),
    "path_not_found": lambda: _with_winerror(FileNotFoundError(errno.ENOENT, "path"), 3),
    "posix_permission": lambda: PermissionError(errno.EACCES, "Permission denied"),
    "other_windows_code": lambda: windows_error(1224, "user-mapped section open"),
}


def _with_winerror(error: OSError, code: int) -> OSError:
    error.winerror = code
    return error


@pytest.mark.parametrize("name", sorted(UNRELATED_ERRORS))
def test_unrelated_errors_fail_at_once(tmp_path, monkeypatch, name):
    path = tmp_path / collector.LATEST_STATUS_FILENAME
    previous = publish_previous(path)
    error = UNRELATED_ERRORS[name]()
    replace, calls = _raising(error)
    monkeypatch.setattr(os, "replace", replace)
    sleeps = Sleeps()

    with pytest.raises(OSError) as raised:
        collector.atomic_json(path, CURRENT, sleep=sleeps)

    assert raised.value is error
    assert len(calls) == 1
    assert sleeps == []
    assert path.read_bytes() == previous
    assert leftovers(tmp_path) == []


def test_a_disk_error_after_a_collision_is_not_retried_further(tmp_path, monkeypatch):
    path = tmp_path / collector.LATEST_STATUS_FILENAME
    publish_previous(path)
    errors = [windows_error(ERROR_ACCESS_DENIED, "Access is denied"), OSError(errno.ENOSPC, "full")]
    calls = []

    def replace(source, destination, *args, **kwargs):
        calls.append(os.fspath(source))
        raise errors[len(calls) - 1]

    monkeypatch.setattr(os, "replace", replace)
    sleeps = Sleeps()

    with pytest.raises(OSError) as raised:
        collector.atomic_json(path, CURRENT, sleep=sleeps)

    assert raised.value is errors[1]
    assert len(calls) == 2 and len(set(calls)) == 1
    assert sleeps == [collector.REPLACE_RETRY_DELAYS_SECONDS[0]]
    assert leftovers(tmp_path) == []


def test_an_interrupt_during_backoff_leaves_no_temporary_file(tmp_path, monkeypatch):
    path = tmp_path / collector.LATEST_STATUS_FILENAME
    previous = publish_previous(path)
    ReplaceFaults({path.name: Rule(READER, failures=None)}).install(monkeypatch)

    def interrupted(seconds):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        collector.atomic_json(path, CURRENT, sleep=interrupted)

    assert path.read_bytes() == previous
    assert leftovers(tmp_path) == []


@pytest.mark.parametrize(
    ("code", "destination", "expected"),
    [
        (ERROR_SHARING_VIOLATION, "file", True),
        (ERROR_SHARING_VIOLATION, "absent", True),
        (ERROR_ACCESS_DENIED, "file", True),
        (ERROR_ACCESS_DENIED, "absent", False),
        (ERROR_ACCESS_DENIED, "directory", False),
        (None, "file", False),
        (2, "file", False),
        (3, "file", False),
        (1224, "file", False),
    ],
)
def test_only_reader_and_scanner_collisions_are_transient(tmp_path, code, destination, expected):
    path = tmp_path / "collector_latest_status.json"
    if destination == "file":
        path.write_text("{}", encoding="utf-8")
    elif destination == "directory":
        path.mkdir()
    error = PermissionError(errno.EACCES, "denied")
    if code is not None:
        error.winerror = code

    assert collector.transient_replace_error(error, path) is expected


@windows_only
def test_a_read_only_destination_is_classified_permanent(tmp_path):
    path = tmp_path / "collector_latest_status.json"
    path.write_text("{}", encoding="utf-8")
    os.chmod(path, stat.S_IREAD)
    try:
        error = windows_error(ERROR_ACCESS_DENIED, "Access is denied")
        assert collector.transient_replace_error(error, path) is False
    finally:
        os.chmod(path, stat.S_IWRITE)


# -- unchanged: sanitized, flushed and fsynced before the replacement --------------


def test_publication_still_sanitizes_and_fsyncs_before_replacing(tmp_path, monkeypatch):
    path = tmp_path / collector.LATEST_STATUS_FILENAME
    events = []
    real_fsync = os.fsync

    def fsync(descriptor):
        events.append("fsync")
        return real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fsync)
    faults = ReplaceFaults(events=events).install(monkeypatch)
    credential_url = "postgresql://" + "user" + ":" + "pw" + "@db/agrosat"

    collector.atomic_json(path, {"note": f"Authorization: Bearer abc.def {credential_url}"})

    assert events[0] == "fsync" and events[1][2] == "replaced"
    assert faults.attempts(path.name) == [(1, "replaced")]
    written = path.read_text(encoding="utf-8")
    assert "abc.def" not in written and "pw@" not in written
    assert "[REDACTED" in written


# -- Part F: the heartbeat file is published the same way --------------------------


class Ticks:
    """Stands in for the publisher's stop event: ``beats`` ticks, then stop."""

    def __init__(self, beats: int):
        self.beats = beats
        self.timeouts = []

    def wait(self, timeout):
        self.timeouts.append(timeout)
        return len(self.timeouts) > self.beats

    def set(self):
        self.beats = 0


def publisher(tmp_path: Path) -> collector.HeartbeatPublisher:
    return collector.HeartbeatPublisher(
        tmp_path / collector.HEARTBEAT_FILENAME, "d" * 32, "0" * 40
    )


def test_a_heartbeat_survives_a_reader_collision(tmp_path, monkeypatch):
    beat = publisher(tmp_path)
    beat._publish()
    faults = ReplaceFaults({beat.path.name: Rule(READER, failures=1)}).install(monkeypatch)

    beat._publish()

    assert [outcome for _, outcome in faults.attempts(beat.path.name)] == [
        "winerror=5",
        "replaced",
    ]
    payload = read(beat.path)
    assert payload["run_id"] == "d" * 32 and payload["heartbeat_at"]
    assert leftovers(tmp_path) == []


def test_stop_survives_a_reader_at_the_final_beat(tmp_path, monkeypatch):
    beat = publisher(tmp_path)
    beat.start()
    faults = ReplaceFaults({beat.path.name: Rule(READER, failures=1)}).install(monkeypatch)

    beat.stop()  # 7d1a975: PermissionError, which run() turned into exit code 4

    assert [outcome for _, outcome in faults.attempts(beat.path.name)][-1] == "replaced"
    assert not beat._thread.is_alive()
    assert leftovers(tmp_path) == []


def test_a_missed_beat_does_not_end_the_heartbeat(tmp_path, monkeypatch):
    """A beat that cannot be persisted is retried at the next tick, not fatal."""
    beat = publisher(tmp_path)
    beat._publish()
    beat._stop = Ticks(beats=3)
    ReplaceFaults(
        {beat.path.name: Rule(DISK_FULL, publications=frozenset({3}), failures=None)}
    ).install(monkeypatch)

    beat._loop()  # 7d1a975: the failed beat raised out of the loop, ending the thread

    assert beat._stop.timeouts == [30, 30, 30, 30]  # the cadence is unchanged
    assert beat.missed_beats == 1
    assert read(beat.path)["run_id"] == "d" * 32
    assert leftovers(tmp_path) == []


def test_the_heartbeat_cadence_is_unchanged(tmp_path):
    beat = publisher(tmp_path)
    beat._stop = Ticks(beats=2)

    beat._loop()

    assert beat._stop.timeouts == [30, 30, 30]
    assert read(beat.path)["release_commit"] == "0" * 40
