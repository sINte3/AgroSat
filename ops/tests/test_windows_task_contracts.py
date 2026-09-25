"""Schedule contracts enforced by the PowerShell task validators (TASK_230 Parts L and M).

The canonical production Sentinel configuration is exactly one daily trigger at
06:00:00; the historical ["06:00:00", "18:00:00"] template is rejected. The
canonical notification task stays every 15 minutes with its execution limits.
Explicit \\AgroSat_TASKnnn_* rehearsal identities keep the bounded generic mode.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is required")
TASKS = Path(__file__).resolve().parents[1] / "windows-task"


def run_validator(tmp_path: Path, common: str, function: str, document: dict) -> subprocess.CompletedProcess:
    path = tmp_path / "configuration.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                           f". '{TASKS / common}'; {function} -ConfigurationPath '{path}' | Out-Null"],
                          capture_output=True, text=True, errors="replace", timeout=60)


def sentinel(**schedule) -> dict:
    document = json.loads((TASKS / "collector-task.example.json").read_text(encoding="utf-8"))
    document["schedule"].update(schedule)
    return document


def notifications(**schedule) -> dict:
    document = json.loads((TASKS / "operational-notifications-task.example.json").read_text(encoding="utf-8"))
    document["schedule"].update(schedule)
    return document


def sentinel_result(tmp_path, document):
    return run_validator(tmp_path, "Task209-CollectorTask.Common.ps1", "Get-Task209CollectorConfiguration", document)


def notifications_result(tmp_path, document):
    return run_validator(tmp_path, "Task221-OperationalNotifications.Common.ps1",
                         "Get-Task221OperationalNotificationsConfiguration", document)


def test_checked_in_sentinel_template_is_the_production_contract(tmp_path):
    document = sentinel()
    assert document["schedule"]["daily_at_local_times"] == ["06:00:00"]
    assert sentinel_result(tmp_path, document).returncode == 0


@pytest.mark.parametrize("times,code", [
    (["06:00:00", "18:00:00"], "SENTINEL_PRODUCTION_TRIGGER_COUNT_REJECTED"),
    (["18:00:00"], "SENTINEL_PRODUCTION_TRIGGER_TIME_REJECTED"),
    (["07:00:00"], "SENTINEL_PRODUCTION_TRIGGER_TIME_REJECTED"),
    (["06:00"], "SENTINEL_PRODUCTION_TRIGGER_TIME_REJECTED"),
    ([], "daily_at_local_times must contain one or two bounded triggers"),
])
def test_production_sentinel_rejects_every_other_schedule(tmp_path, times, code):
    result = sentinel_result(tmp_path, sentinel(daily_at_local_times=times))
    assert result.returncode != 0 and code in result.stderr + result.stdout


@pytest.mark.parametrize("change,code", [
    ({"restart_count": 5}, "SENTINEL_PRODUCTION_LIMIT_REJECTED:restart_count"),
    ({"execution_time_limit_hours": 12}, "SENTINEL_PRODUCTION_LIMIT_REJECTED:execution_time_limit_hours"),
    ({"start_when_available": False}, "SENTINEL_PRODUCTION_START_WHEN_AVAILABLE_REJECTED"),
])
def test_production_sentinel_execution_contract_is_fixed(tmp_path, change, code):
    result = sentinel_result(tmp_path, sentinel(**change))
    assert result.returncode != 0 and code in result.stderr + result.stdout


def test_production_sentinel_runs_as_system(tmp_path):
    document = sentinel()
    document["execution_sid"] = "S-1-5-21-1000"
    result = sentinel_result(tmp_path, document)
    assert result.returncode != 0 and "SENTINEL_PRODUCTION_IDENTITY_REJECTED" in result.stderr + result.stdout


def test_rehearsal_identity_keeps_the_generic_bounded_mode_and_others_are_refused(tmp_path):
    document = sentinel(daily_at_local_times=["06:00:00", "18:00:00"])
    document["task_name"] = "\\AgroSat_TASK230_SentinelCycle"
    assert sentinel_result(tmp_path, document).returncode == 0
    document["task_name"] = "\\AgroSat_Sentinel_Other"
    result = sentinel_result(tmp_path, document)
    assert result.returncode != 0 and "rehearsal identity" in result.stderr + result.stdout


def test_installer_preview_refuses_the_historical_two_trigger_template(tmp_path):
    path = tmp_path / "collector-task.json"
    path.write_text(json.dumps(sentinel(daily_at_local_times=["06:00:00", "18:00:00"])), encoding="utf-8")
    result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                             str(TASKS / "Install-CollectorTask.ps1"), "-ConfigurationPath", str(path)],
                            capture_output=True, text=True, errors="replace", timeout=60)
    assert result.returncode != 0 and "SENTINEL_PRODUCTION_TRIGGER_COUNT_REJECTED" in result.stderr + result.stdout


def test_checked_in_notification_template_is_the_production_contract(tmp_path):
    assert notifications_result(tmp_path, notifications()).returncode == 0


@pytest.mark.parametrize("change,code", [
    ({"interval_minutes": 30}, "NOTIFICATIONS_PRODUCTION_SCHEDULE_REJECTED:interval_minutes"),
    ({"interval_minutes": 5}, "NOTIFICATIONS_PRODUCTION_SCHEDULE_REJECTED:interval_minutes"),
    ({"execution_time_limit_minutes": 20}, "NOTIFICATIONS_PRODUCTION_SCHEDULE_REJECTED:execution_time_limit_minutes"),
    ({"restart_count": 1}, "NOTIFICATIONS_PRODUCTION_SCHEDULE_REJECTED:restart_count"),
    ({"restart_interval_minutes": 15}, "NOTIFICATIONS_PRODUCTION_SCHEDULE_REJECTED:restart_interval_minutes"),
    ({"start_when_available": False}, "NOTIFICATIONS_PRODUCTION_START_WHEN_AVAILABLE_REJECTED"),
])
def test_production_notification_schedule_cannot_drift(tmp_path, change, code):
    result = notifications_result(tmp_path, notifications(**change))
    assert result.returncode != 0 and code in result.stderr + result.stdout


def test_production_notifications_run_as_system(tmp_path):
    document = notifications()
    document["execution_sid"] = "S-1-5-21-1000"
    result = notifications_result(tmp_path, document)
    assert result.returncode != 0 and "NOTIFICATIONS_PRODUCTION_IDENTITY_REJECTED" in result.stderr + result.stdout
