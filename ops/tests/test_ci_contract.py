"""The CI contract (TASK_230 Parts U and V): what the workflows must and must not do."""
from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
RUNTIME = json.loads((ROOT / "ops" / "release" / "runtime-contract.json").read_text(encoding="utf-8"))


def load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def steps(workflow: dict) -> list[dict]:
    return [step for job in workflow["jobs"].values() for step in job.get("steps", [])]


def commands(workflow: dict) -> str:
    return "\n".join(step.get("run", "") for step in steps(workflow))


def test_workflows_use_only_the_repository_runtime_contract():
    for name in ("ci.yml", "postgres.yml"):
        for step in steps(load(name)):
            uses = step.get("uses", "")
            if uses.startswith("actions/setup-python"):
                assert step["with"]["python-version"] == RUNTIME["python"]["series"]
            if uses.startswith("actions/setup-node"):
                assert step["with"]["node-version"] == str(RUNTIME["node"]["major"])


def test_workflows_hold_no_secret_force_or_production_target():
    for name in ("ci.yml", "postgres.yml"):
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        workflow = load(name)
        assert "secrets." not in text and "--force" not in commands(workflow)
        assert workflow["permissions"] == {"contents": "read"}
        for forbidden in ("C:\\AgroSat", "AgroSat_PROGRAM_R3", ":8000", ":5173", "Register-ScheduledTask",
                          "/agrosat\n", "agrosat_task"):
            assert forbidden not in text, forbidden
        for job in workflow["jobs"].values():
            assert 0 < job["timeout-minutes"] <= 45


def test_fast_lane_covers_the_required_checks():
    workflow = load("ci.yml")
    run = commands(workflow)
    assert "rollback-contract" in run
    assert "tests/test_web_startup_safety.py" in run
    for retired in ("tests/test_field_inspections.py", "tests/test_satellite_write_safety.py",
                    "tests/test_task209_operational_closure_backend.py", "tests/test_task225_contracts.py"):
        assert retired in run
    assert "python -m pytest tests -q" in run and "python -m pytest ops/tests -q" in run
    assert "Parser]::ParseFile" in run
    assert "npm ci" in run and "npm run test:contracts" in run and "npm run build" in run
    assert "npm audit --audit-level=high" in run
    assert workflow["jobs"]["ops"]["runs-on"] == "windows-latest"  # real Job Objects, never a Linux mock
    assert workflow["concurrency"]["cancel-in-progress"] is True


def test_postgres_lane_is_isolated_and_runs_one_process_per_file():
    workflow = load("postgres.yml")
    job = workflow["jobs"]["postgis"]
    service = job["services"]["postgis"]
    assert service["image"].startswith(f"postgis/postgis:{RUNTIME['postgresql']['server_major']}-")
    assert service["env"]["POSTGRES_DB"].startswith("agrosat_h0a")
    assert job["env"]["AGROSAT_TEST_DATABASE_URL"].endswith("/agrosat_h0a_ci")
    run = commands(workflow)
    assert "alembic upgrade head" in run and "alembic heads" in run
    assert 'for file in tests/*postgres*.py' in run


KEY_STEP = "Job-scoped ephemeral SECRET_KEY (test only)"


def runs_backend_tests(job: dict) -> bool:
    return any(step.get("working-directory") == "backend" and "pytest" in step.get("run", "")
               for step in job["steps"])


def test_backend_jobs_draw_their_own_masked_secret_key():
    """TASK_256: main.py refuses an empty SECRET_KEY, so every job that runs backend tests draws a
    fresh key right after setup-python and masks it before exporting it through GITHUB_ENV. No key
    is written into a workflow or shared between jobs; a job that never imports the backend gets none."""
    for name in ("ci.yml", "postgres.yml"):
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        workflow = load(name)
        assert re.search(r"SECRET_KEY\s*[:=]\s*[^{\s]", text) is None
        assert "SECRET_KEY" not in (workflow.get("env") or {})
        for job in workflow["jobs"].values():
            assert "SECRET_KEY" not in (job.get("env") or {})
            names = [step.get("name", step.get("uses")) for step in job["steps"]]
            if not runs_backend_tests(job):
                assert KEY_STEP not in names
                continue
            assert names.count(KEY_STEP) == 1
            index = names.index(KEY_STEP)
            assert job["steps"][index - 1]["uses"].startswith("actions/setup-python")
            step = job["steps"][index]
            script = step["run"]
            assert step["shell"] == "python" and "token_urlsafe(48)" in script
            assert 'if "SECRET_KEY" in os.environ' in script
            assert script.index("::add-mask::") < script.index('os.environ["GITHUB_ENV"]')
