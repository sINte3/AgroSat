"""Static contract for review-only Windows Scheduled Task artifacts."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TASK_ROOT = ROOT / "ops" / "windows-task"


def _read(name: str) -> str:
    return (TASK_ROOT / name).read_text(encoding="utf-8")


def test_required_task_artifacts_exist():
    required = {
        "collector-task.example.json",
        "Task209-CollectorTask.Common.ps1",
        "Invoke-Collector.ps1",
        "Install-CollectorTask.ps1",
        "Inspect-CollectorTask.ps1",
        "Disable-CollectorTask.ps1",
        "Uninstall-CollectorTask.ps1",
        "Test-CollectorTask.ps1",
        "README.md",
    }
    assert required.issubset({path.name for path in TASK_ROOT.iterdir()})


def test_example_configuration_is_bounded_and_secret_free():
    configuration = json.loads(_read("collector-task.example.json"))
    assert configuration["execution_identity"].endswith("$")
    assert "<DOMAIN>" in configuration["execution_identity"]
    assert configuration["expected_windows_timezone_id"] == "West Asia Standard Time"
    assert configuration["schedule"]["multiple_instances"] == "IgnoreNew"
    assert 1 <= configuration["collector"]["batch_size"] <= 100
    assert 1 <= configuration["collector"]["lookback_days"] <= 30
    assert 1 <= configuration["collector"]["max_attempts"] <= 5
    serialized = json.dumps(configuration).lower()
    assert "password" not in serialized
    assert "token" not in serialized
    assert "authorization" not in serialized


def test_runner_calls_only_canonical_collector_with_explicit_mode_and_bounds():
    source = _read("Invoke-Collector.ps1")
    assert "backend\\scripts\\collect_satellite.py" in source
    for mode in ('"dry-run"', '"diagnostic"', '"apply"'):
        assert mode in source
    for argument in (
        "--all-active-fields",
        "--indices",
        "--batch-size",
        "--lookback-days",
        "--max-attempts",
        "--field-timeout-seconds",
        "--cycle-timeout-seconds",
        "--output-dir",
        "--state-dir",
        "--lock-dir",
    ):
        assert argument in source
    assert "AGROSAT_RUNTIME_ENV_FILE" in source


def test_only_install_script_can_register_task():
    scripts = tuple(TASK_ROOT.glob("*.ps1"))
    registrars = [
        script.name
        for script in scripts
        if "Register-ScheduledTask" in script.read_text(encoding="utf-8")
    ]
    assert registrars == ["Install-CollectorTask.ps1"]


def test_mutating_scripts_require_apply_and_should_process():
    commands = {
        "Install-CollectorTask.ps1": "Register-ScheduledTask",
        "Disable-CollectorTask.ps1": "Disable-ScheduledTask",
        "Uninstall-CollectorTask.ps1": "Unregister-ScheduledTask",
    }
    for name, command in commands.items():
        source = _read(name)
        assert "[switch]$Apply" in source
        assert "if (-not $Apply)" in source
        assert "SupportsShouldProcess" in source
        assert "$PSCmdlet.ShouldProcess" in source
        assert command in source


def test_install_contract_is_gmsa_single_instance_and_no_overwrite():
    source = _read("Install-CollectorTask.ps1")
    assert "-LogonType ServiceAccount" in source
    assert "-MultipleInstances IgnoreNew" in source
    assert "-ExecutionTimeLimit" in source
    assert "-RestartCount" in source
    assert "-RestartInterval" in source
    assert "-StartWhenAvailable" in source
    assert "-Mode\", \"apply\"" in source
    assert "-Force" not in source


def test_smoke_test_is_no_write():
    source = _read("Test-CollectorTask.ps1")
    assert '-Mode "dry-run"' in source
    assert '-Mode "diagnostic"' not in source
    assert '-Mode "apply"' not in source
