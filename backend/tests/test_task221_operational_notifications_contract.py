"""Focused contracts for TASK_221 notification reconciliation."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from services import operational_notifications as service
from scripts import reconcile_operational_notifications as command


def test_notification_identity_is_deterministic_and_recipient_specific():
    fact = {
        "enterprise_id": 4,
        "field_id": 8,
        "recipient_user_id": 10,
        "recipient_role_snapshot": "manager",
        "case_key": "inspection:12",
        "source_kind": "agronomy_work_item",
        "source_id": "14",
        "notification_type": "overdue",
        "severity": "critical",
        "title": "Просрочена работа",
        "message": "Срок истёк.",
        "provenance": {"cycle": 1},
        "source_cycle": "due:123",
        "due_at": datetime(2026, 9, 20, tzinfo=timezone.utc),
    }
    first = service._notification_record(fact, datetime(2026, 9, 21, tzinfo=timezone.utc))
    second = service._notification_record(fact, datetime(2026, 9, 22, tzinfo=timezone.utc))
    assert first["dedupe_key"] == second["dedupe_key"]
    assert first["payload_fingerprint"] != second["payload_fingerprint"]
    assert len(first["dedupe_key"]) == 64
    other = service._notification_record({**fact, "recipient_user_id": 11}, datetime(2026, 9, 21, tzinfo=timezone.utc))
    assert other["dedupe_key"] != first["dedupe_key"]


def test_candidate_contract_is_bounded_and_has_every_required_type():
    sql = service.NOTIFICATION_CANDIDATES_SQL
    assert "LIMIT :limit" in sql
    for value in (
        "new_critical",
        "assignment",
        "due_soon",
        "overdue",
        "missing_execution_evidence",
        "awaiting_satellite_verification",
        "external_source_unavailable",
    ):
        assert value in sql
    assert "u.is_active=true" in sql
    assert "u.role IN ('admin','manager','agronomist')" in sql
    assert "viewer" not in sql


def test_resolution_contract_is_source_owned_and_bounded():
    sql = service.STALE_ACTIVE_SQL
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "LIMIT :limit" in sql
    assert "satellite_collection_runs" in sql
    assert "agronomy_work_items" in sql
    assert "status IN ('unread','read')" in sql


def test_reconciler_uses_transaction_scoped_advisory_lock():
    source = Path(service.__file__).read_text(encoding="utf-8")
    assert "pg_try_advisory_xact_lock" in source
    assert "pg_try_advisory_lock(" not in source


def test_reconcile_rejects_unbounded_limit_before_sql():
    class NoDatabase:
        def execute(self, *_args, **_kwargs):
            raise AssertionError("database must not be reached")

    with pytest.raises(ValueError, match="between 1 and 500"):
        service.reconcile_notifications(NoDatabase(), apply=False, limit=501)


def test_cli_requires_explicit_mode_and_bounded_limit():
    with pytest.raises(SystemExit):
        command.parse_args(["--output-dir", "C:\\outside"])
    args = command.parse_args(["--dry-run", "--limit", "200", "--output-dir", "C:\\outside"])
    assert args.dry_run is True
    assert args.apply is False


def test_cli_output_must_remain_outside_repository(tmp_path, monkeypatch):
    inside = Path(command.REPO_ROOT) / "runtime"
    with pytest.raises(command.ContractError, match="outside"):
        command.external_directory(str(inside))
    outside = tmp_path.resolve()
    monkeypatch.setattr(command, "REPO_ROOT", tmp_path / "different-repository")
    assert command.external_directory(str(outside)) == outside


def test_no_scheduler_or_external_delivery_in_reconciler_source():
    source = Path(service.__file__).read_text(encoding="utf-8").lower()
    cli = Path(command.__file__).read_text(encoding="utf-8").lower()
    assert "apscheduler" not in source + cli
    assert "telegram" not in source + cli
    assert "wialon" not in source + cli
    assert "requests." not in source + cli
    assert "httpx." not in source + cli


def test_windows_task_contract_is_release_bound_and_installed_disabled():
    root = Path(__file__).resolve().parents[2] / "ops" / "windows-task"
    common = (root / "Task221-OperationalNotifications.Common.ps1").read_text(encoding="utf-8")
    invoke = (root / "Invoke-OperationalNotifications.ps1").read_text(encoding="utf-8")
    install = (root / "Install-OperationalNotificationsTask.ps1").read_text(encoding="utf-8")
    assert "\\AgroSat_PROGRAM_R3_OperationalNotifications" in common
    assert "working_directory must be the exact immutable release directory" in common
    assert "release-manifest.json" in invoke
    assert "--apply" in invoke and "--dry-run" in invoke
    assert "Disable-ScheduledTask" in install
    assert "MultipleInstances IgnoreNew" in install
    assert "ExecutionTimeLimit" in install
    assert "RestartCount" in install
