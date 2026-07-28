"""Contract tests for the canonical TASK_209 satellite collector."""

import json
from pathlib import Path
import tempfile

import pytest

from scripts import collect_satellite as collector


def invocation(root: Path, *extra: str):
    return collector.parse_args(
        [
            "--dry-run",
            "--field-ids",
            "4,5",
            "--indices",
            "ndvi,savi,ndmi",
            "--output-dir",
            str(root / "output"),
            "--state-dir",
            str(root / "state"),
            "--lock-dir",
            str(root / "locks"),
            *extra,
        ]
    )


def test_requires_explicit_mode_scope_indices_and_artifact_directories():
    with pytest.raises(SystemExit):
        collector.parse_args([])


def test_index_and_field_bounds_are_deterministic():
    assert collector.parse_indices("NDVI,savi,ndvi") == ["ndvi", "savi"]
    assert collector.parse_field_ids("4,5,4") == [4, 5]
    with pytest.raises(collector.ContractError):
        collector.parse_indices("ndvi,unsupported")
    with pytest.raises(collector.ContractError):
        collector.parse_field_ids(",".join(str(value) for value in range(1, 102)))


def test_artifact_directories_must_be_external_absolute_paths():
    with pytest.raises(collector.ContractError):
        collector.external_directory("relative", "--output-dir")
    with pytest.raises(collector.ContractError):
        collector.external_directory(
            str(collector.REPO_ROOT / "evidence"),
            "--output-dir",
        )


def test_mode_mapping_is_explicit():
    assert collector.child_mode("dry-run") == "--dry-run"
    assert collector.child_mode("diagnostic") == "--apply"
    assert collector.child_mode("apply") == "--write"


def test_all_active_scope_requires_bounded_batch():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        args = collector.parse_args(
            [
                "--dry-run",
                "--all-active-fields",
                "--indices",
                "ndvi",
                "--output-dir",
                str(root / "output"),
                "--state-dir",
                str(root / "state"),
                "--lock-dir",
                str(root / "locks"),
            ]
        )
        with pytest.raises(collector.ContractError):
            collector.validate(args)


def test_builds_separate_ndvi_and_multi_commands_without_secrets():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        args = invocation(root)
        plan = collector.validate(args)
        ndvi = collector.build_child_command("ndvi", args, plan, root / "ndvi")
        multi = collector.build_child_command("multi", args, plan, root / "multi")

    assert "run_ndvi_collection_cycle.py" in " ".join(ndvi)
    assert "run_multi_index_collection_cycle.py" in " ".join(multi)
    assert "--indices" in multi
    assert multi[multi.index("--indices") + 1] == "savi,ndmi"
    joined = " ".join(ndvi + multi).lower()
    assert "password" not in joined
    assert "authorization" not in joined


def test_success_writes_one_sanitized_parent_summary():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        calls = []

        def child(command, timeout):
            calls.append((command, timeout))
            return {"exit_code": 0, "timed_out": False, "stdout": "ok", "stderr": ""}

        args = invocation(root)
        code, summary = collector.run(
            args,
            child_runner=child,
            lock_acquire=lambda *args, **kwargs: "lock",
            lock_release=lambda lock: None,
            run_id_factory=lambda: "run123",
        )

        summary_path = root / "output" / "run_run123" / "collector_summary.json"
        persisted = json.loads(summary_path.read_text(encoding="utf-8"))
        latest_path = root / "output" / collector.LATEST_STATUS_FILENAME
        latest = json.loads(latest_path.read_text(encoding="utf-8"))

    assert code == 0
    assert len(calls) == 2
    assert [item["provider"] for item in summary["children"]] == ["ndvi", "multi"]
    assert persisted["exit_code"] == 0
    assert persisted["run_id"] == "run123"
    assert latest["status"] == "succeeded"
    assert latest["failure_category"] is None
    assert latest["providers"] == [
        {"exit_code": 0, "provider": "ndvi", "timed_out": False},
        {"exit_code": 0, "provider": "multi", "timed_out": False},
    ]
    assert "stdout" not in json.dumps(latest)
    assert "stderr" not in json.dumps(latest)
    assert "diagnostics" not in latest


def test_latest_status_is_running_while_child_executes():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        observed = []

        def child(command, timeout):
            latest_path = root / "output" / collector.LATEST_STATUS_FILENAME
            observed.append(json.loads(latest_path.read_text(encoding="utf-8")))
            return {"exit_code": 0, "timed_out": False}

        code, _ = collector.run(
            invocation(root),
            child_runner=child,
            lock_acquire=lambda *args, **kwargs: "lock",
            lock_release=lambda lock: None,
            run_id_factory=lambda: "heartbeat",
        )

    assert code == 0
    assert observed
    assert all(item["status"] == "running" for item in observed)
    assert all(item["finished_at"] is None for item in observed)


def test_failure_categories_are_bounded_labels():
    base = {
        "run_id": "classification",
        "mode": "diagnostic",
        "started_at": "2026-07-28T00:00:00+00:00",
        "finished_at": "2026-07-28T00:00:01+00:00",
        "duration_seconds": 1,
        "diagnostics": [],
    }
    cases = (
        ({"exit_code": 4, "children": [{"stderr": "HTTP 401"}]}, "auth"),
        ({"exit_code": 4, "children": [{"stderr": "HTTP 429"}]}, "quota"),
        ({"exit_code": 1, "children": [{"stderr": "cloud"}]}, "cloud"),
        (
            {"exit_code": 1, "children": [{"timed_out": True}]},
            "network",
        ),
        ({"exit_code": 2, "children": []}, "contract"),
        ({"exit_code": 3, "children": []}, "lock_contention"),
        ({"exit_code": 130, "children": []}, "cancelled"),
    )
    for updates, expected in cases:
        snapshot = collector.operational_snapshot({**base, **updates})
        assert snapshot["failure_category"] == expected


def test_diagnostic_mode_never_maps_to_child_write():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        args = collector.parse_args(
            [
                "--diagnostic",
                "--field-ids",
                "4",
                "--indices",
                "ndvi,savi",
                "--output-dir",
                str(root / "output"),
                "--state-dir",
                str(root / "state"),
                "--lock-dir",
                str(root / "locks"),
            ]
        )
        seen = []

        def child(command, timeout):
            seen.append(command)
            return {"exit_code": 0, "timed_out": False}

        code, _ = collector.run(
            args,
            child_runner=child,
            lock_acquire=lambda *args, **kwargs: "lock",
            lock_release=lambda lock: None,
            run_id_factory=lambda: "diagnostic",
        )

    assert code == 0
    assert seen
    assert all("--apply" in command for command in seen)
    assert all("--write" not in command for command in seen)


def test_child_fatal_contract_error_stops_remaining_provider():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        calls = []

        def child(command, timeout):
            calls.append(command)
            return {"exit_code": 2, "timed_out": False}

        code, summary = collector.run(
            invocation(root),
            child_runner=child,
            lock_acquire=lambda *args, **kwargs: "lock",
            lock_release=lambda lock: None,
            run_id_factory=lambda: "fatal",
        )

    assert code == 2
    assert len(calls) == 1
    assert summary["children"][0]["provider"] == "ndvi"


def test_keyboard_cancellation_is_structured_and_releases_lock():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        released = []

        def child(command, timeout):
            raise KeyboardInterrupt

        code, summary = collector.run(
            invocation(root),
            child_runner=child,
            lock_acquire=lambda *args, **kwargs: "lock",
            lock_release=released.append,
            run_id_factory=lambda: "cancelled",
        )

    assert code == 130
    assert summary["diagnostics"] == ["CANCELLED"]
    assert released == ["lock"]
