"""Release, qualification, and rollback safety contracts for TASK_221 (current control plane)."""

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / "ops" / "release"
QUALIFICATION = ROOT / "ops" / "qualification"
if str(RELEASE) not in sys.path:
    sys.path.insert(0, str(RELEASE))

from controlplane.common import ControlPlaneError  # noqa: E402
from controlplane.profiles import REHEARSAL_DATABASE_PATTERN, load_rehearsal_profile  # noqa: E402


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_task221_current_release_path_carries_no_historical_macrostage_identity():
    for path in RELEASE.rglob("*"):
        if path.suffix not in {".py", ".ps1", ".json", ".md"}:
            continue
        text = read(path)
        for stale in ("macrostage", "MACROSTAGE", "task/program-r3-", "387eaeda6bcbcc3ef0a2e2951b8f87bbf75ad927",
                      "40e8e379d9d29cb4bfb8afebdd9c489c19756fac", "f3a95f4e4d97b025a967ae3812603e5aae0d969d",
                      "agrosat_r3_task22"):
            assert stale not in text, (path.name, stale)


def rehearsal_profile(tmp_path: Path, database: str, env_database: str) -> Path:
    root = tmp_path / "rehearsal"
    root.mkdir(exist_ok=True)
    (root / ".agrosat-rehearsal-root.json").write_text(json.dumps({"profile_id": "TASK221"}))
    env = root / "rehearsal.env"
    env.write_text(f"DATABASE_URL=postgresql://u:p@localhost:5432/{env_database}\n")
    document = {"schema_version": 1, "kind": "agrosat_rehearsal_profile", "profile_id": "TASK221",
                "rehearsal_root": str(root), "release_root": str(root / "r"), "runtime_root": str(root / "t"),
                "control_root": str(root / "c"), "runtime_env_file": str(env), "database_name": database,
                "backend_port": 58221, "frontend_port": 58222,
                "tasks": {"backend": "\\AgroSat_TASK221_Backend", "frontend": "\\AgroSat_TASK221_Frontend",
                          "sentinel": "\\AgroSat_TASK221_Sentinel", "notifications": "\\AgroSat_TASK221_Notifications"},
                "node_executable": r"C:\Program Files\nodejs\node.exe", "pg_bin": r"C:\Program Files\PostgreSQL\16\bin",
                "signing_thumbprints": ["816767BE400FE53327432B12B29FE4B5809CA4CA"],
                "timezone_id": "West Asia Standard Time", "fault_injection": None, "worker_dry_run": False,
                "backup_task": None}
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(document))
    return path


def test_task221_rehearsal_database_namespace_is_explicitly_guarded(tmp_path):
    assert REHEARSAL_DATABASE_PATTERN.fullmatch("agrosat_task221_contract")
    for rejected in ("agrosat", "agrosat_r3_task220_contract", "agrosat_h0a_task229", "agrosat_task22_x"):
        assert not REHEARSAL_DATABASE_PATTERN.fullmatch(rejected)
    accepted = load_rehearsal_profile(rehearsal_profile(tmp_path, "agrosat_task221_contract", "agrosat_task221_contract"))
    assert accepted.database_name == "agrosat_task221_contract"
    with pytest.raises(ControlPlaneError) as caught:
        load_rehearsal_profile(rehearsal_profile(tmp_path, "agrosat_task221_contract", "agrosat_task220_contract"))
    assert caught.value.code == "REHEARSAL_DATABASE_GUARD_FAILED"


def test_task221_rollback_contract_classifies_populated_history_as_restore_only():
    contract = json.loads(read(RELEASE / "rollback-contract.json"))
    classifications = {item["revision"]: item for item in contract["migrations"]}
    task221 = classifications["0016_operational_command_center"]
    assert task221["path"] == "backend/alembic/versions/0016_operational_command_center.py"
    assert task221["classification"] == "destructive_after_data"
    assert task221["automatic_downgrade_allowed"] is False
    assert task221["rollback_strategy"] == "restore_validated_pre_release_backup_or_roll_forward"
    assert any("notification" in item for item in task221["data_at_risk"])


def test_task221_protected_qualification_tools_are_fail_closed():
    protected = read(QUALIFICATION / "Run-Task221Protected.py")
    database = read(QUALIFICATION / "Run-Task221DatabaseQualification.py")
    runtime = read(QUALIFICATION / "Prepare-Task221RuntimeDatabase.py")
    browser = read(QUALIFICATION / "Run-Task221BrowserProtected.py")
    workflow = read(QUALIFICATION / "Run-Task221WorkflowQualification.py")
    for source in (protected, database, runtime, browser, workflow):
        assert "agrosat_r3_task221_" in source
        assert "TASK221_DATABASE_IDENTITY_REJECTED" in source
    assert '"WIALON_ENABLED": "false"' in protected
    assert '"TELEGRAM_NOTIFICATIONS_ENABLED": "false"' in protected
    assert "0016_operational_command_center" in database
    assert "0016_operational_command_center" in runtime
    assert "populated operational history downgrade must fail closed" in workflow
    assert '"database_urls_included": False' in database


def test_task221_release_and_qualification_sources_do_not_mutate_git_history():
    combined = "\n".join(
        read(path).lower()
        for root in (RELEASE, QUALIFICATION)
        for path in root.rglob("*")
        if path.suffix in {".ps1", ".py", ".json", ".mjs"}
    )
    for forbidden in (
        "git reset",
        "git rebase",
        "git checkout",
        "git switch",
        "git push --force",
        "git commit --amend",
    ):
        assert forbidden not in combined
