"""Release manifest, archive and rollback contracts (TASK_209 intent, TASK_230 control plane).

A release is identified by exact immutable facts (candidate SHA, the exact
production SHA it replaces, fetched remote refs, ancestry, archive SHA-256,
the candidate's own Alembic head, the runtime contract), never by historical
branch or worktree names. Rollback classification covers every shipped
migration, and the release tooling never rewrites Git history, registers
Scheduled Tasks, or downgrades a destructive migration.
"""
from __future__ import annotations

import json
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "ops"
RELEASE = OPS / "release"
for path in (RELEASE, OPS / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from controlplane import migration  # noqa: E402
from controlplane.common import ControlPlaneError  # noqa: E402
from controlplane.gitmaterial import Git, extract_archive  # noqa: E402
from controlplane.manifest import build_manifest, load_runtime_contract, source_identity  # noqa: E402
from controlplane.state import RELEASE_GATES, ROLLBACK_GATES  # noqa: E402
from fakehost import graph_until  # noqa: E402

POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


def git(repository: Path, *arguments: str) -> str:
    return subprocess.run(["git", "-C", str(repository), *arguments], check=True, capture_output=True,
                          text=True).stdout.strip()


def repository(tmp_path: Path, *, secret: bool = False):
    origin, repo = tmp_path / "origin.git", tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    git(repo, "config", "core.autocrlf", "false")
    git(repo, "remote", "add", "origin", str(origin))
    commits = []
    for index in range(2):
        (repo / "backend").mkdir(exist_ok=True)
        (repo / "backend" / "requirements.txt").write_text("fastapi\n")
        (repo / "frontend").mkdir(exist_ok=True)
        (repo / "frontend" / "app.js").write_text(f"// {index}\n")
        if secret and index == 1:
            (repo / "backend" / ".env").write_text("SECRET_KEY=x\n")
        git(repo, "add", "-A")
        git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", f"c{index}")
        commits.append(git(repo, "rev-parse", "HEAD"))
    return repo, commits


def test_release_tooling_compiles_and_every_ops_powershell_file_parses():
    for path in sorted(RELEASE.rglob("*.py")):
        py_compile.compile(str(path), doraise=True)
    if POWERSHELL is None:
        pytest.skip("PowerShell parser unavailable")
    scripts = sorted(path for path in OPS.rglob("*.ps1"))
    assert scripts
    for script in scripts:
        command = ("$errors=$null; [System.Management.Automation.Language.Parser]::ParseFile("
                   f"'{script}',[ref]$null,[ref]$errors) | Out-Null; if($errors.Count){{exit 2}}")
        subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", command], check=True,
                       capture_output=True, text=True, errors="replace", timeout=30)


def test_release_archive_extraction_covers_the_safe_immutable_archive_contract(tmp_path):
    archive = tmp_path / "archive with spaces.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("app/readme.txt", "safe")
    assert extract_archive(archive, tmp_path / "extract with spaces") == 1
    assert (tmp_path / "extract with spaces" / "app" / "readme.txt").read_text() == "safe"
    with pytest.raises(ControlPlaneError) as caught:
        extract_archive(archive, tmp_path / "extract with spaces")
    assert caught.value.code == "IMMUTABLE_RELEASE_ALREADY_EXISTS"
    for name, code in (("../escape.txt", "SOURCE_ARCHIVE_UNSAFE_PATH"), ("C:/escape.txt", "SOURCE_ARCHIVE_UNSAFE_PATH"),
                       ("dir./x.txt", "SOURCE_ARCHIVE_UNSAFE_PATH")):
        unsafe = tmp_path / f"unsafe{len(name)}.zip"
        with zipfile.ZipFile(unsafe, "w") as package:
            package.writestr(name, "blocked")
        with pytest.raises(ControlPlaneError) as caught:
            extract_archive(unsafe, tmp_path / f"out{len(name)}")
        assert caught.value.code == code
        assert not (tmp_path / "escape.txt").exists()


def test_manifest_identity_is_exact_sha_ancestry_and_archive_bound(tmp_path):
    repo, (current, candidate) = repository(tmp_path)
    git(repo, "push", "-q", "origin", "HEAD:refs/heads/task/any-name")
    identity = source_identity(Git(repo), candidate_sha=candidate, expected_current_sha=current, fetch=True)
    assert identity["candidate_sha"] == candidate and identity["expected_current_sha"] == current
    assert identity["containing_remote_refs"] == ["origin/task/any-name"]
    assert identity["commits_since_current"] == 1 and identity["tree_sha"] == git(repo, "rev-parse", f"{candidate}^{{tree}}")
    assert identity["changed_since_current"]["frontend_changed"] is True
    archive = Git(repo).archive(candidate, tmp_path / "source.zip")
    assert archive["sha256"] and archive["bytes"] > 0
    manifest = build_manifest(identity=identity, archive=archive, graph=graph_until("0016_operational_command_center"),
                              runtime_contract=load_runtime_contract(), release_id="R209-manifest-0001")
    assert manifest["schema_version"] == 2 and manifest["git_sha"] == candidate
    assert manifest["alembic"]["head"] == "0016_operational_command_center" and manifest["alembic"]["head_count"] == 1
    assert manifest["source_archive"]["sha256"] == archive["sha256"]
    assert manifest["runtime_contract"]["python"]["series"] == "3.14"
    text = json.dumps(manifest)
    for stale in ("program_branch", "macrostage", "accepted_source_baseline", "source_checkout"):
        assert stale not in text


def test_manifest_refuses_unpublished_non_descendant_and_unsafe_candidates(tmp_path):
    repo, (current, candidate) = repository(tmp_path)
    with pytest.raises(ControlPlaneError) as caught:
        source_identity(Git(repo), candidate_sha=candidate, expected_current_sha=current, fetch=True)
    assert caught.value.code == "CANDIDATE_NOT_PUBLISHED"
    git(repo, "push", "-q", "origin", "HEAD:refs/heads/main")
    with pytest.raises(ControlPlaneError) as caught:
        source_identity(Git(repo), candidate_sha=current, expected_current_sha=candidate, fetch=True)
    assert caught.value.code == "CANDIDATE_ANCESTRY_REJECTED"
    with pytest.raises(ControlPlaneError) as caught:
        source_identity(Git(repo), candidate_sha=candidate[:12], expected_current_sha=current, fetch=False)
    assert caught.value.code == "CANDIDATE_SHA_MALFORMED"
    secret_repo, (base, leaked) = repository(tmp_path / "secret", secret=True)
    git(secret_repo, "push", "-q", "origin", "HEAD:refs/heads/main")
    with pytest.raises(ControlPlaneError) as caught:
        source_identity(Git(secret_repo), candidate_sha=leaked, expected_current_sha=base, fetch=True)
    assert caught.value.code == "CANDIDATE_MATERIAL_UNSAFE"


def test_release_cli_requires_explicit_full_shas_and_fixed_production_identity(capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("controlplane_cli", RELEASE / "Invoke-AgroSatControlPlane.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    common = ["--release-id", "R209-cli-000001", "--expected-current", "b" * 40, "--authorization", r"C:\x.json"]

    def code(arguments):
        assert cli.main(arguments) == 2
        return json.loads(capsys.readouterr().out)["error"]["code"]

    assert code(["release", "--mode", "production", "--candidate", "abc1234", *common]) == "CANDIDATE_SHA_MALFORMED"
    assert code(["release", "--mode", "rehearsal", "--candidate", "a" * 40, *common]) == "REHEARSAL_PROFILE_REQUIRED"
    assert code(["release", "--mode", "production", "--profile", r"C:\p.json", "--candidate", "a" * 40,
                 *common]) == "PRODUCTION_PROFILE_IS_FIXED"
    assert code(["release", "--mode", "production", "--no-fetch", "--candidate", "a" * 40,
                 *common]) == "PRODUCTION_OPTION_REJECTED"


def test_release_state_machine_has_explicit_ordered_gates_ending_in_commit():
    assert RELEASE_GATES == ("PRECHECK", "BACKUP", "MATERIALIZE", "VALIDATE", "MIGRATION_PLAN", "MIGRATE",
                             "SWITCH_BACKEND", "VERIFY_BACKEND", "SWITCH_FRONTEND", "VERIFY_FRONTEND",
                             "REBIND_WORKERS", "VERIFY_WORKERS", "FINAL_HEALTH", "COMMIT")
    assert ROLLBACK_GATES[0] == "PRECHECK" and ROLLBACK_GATES[-1] == "COMMIT"
    assert ROLLBACK_GATES.index("DATABASE") < ROLLBACK_GATES.index("SWITCH_BACKEND")


def test_rollback_contract_covers_every_required_component_and_migration():
    contract = migration.load_rollback_contract()
    assert {item["name"] for item in contract["components"]} == {
        "application", "migration", "collector", "frontend_assets", "scheduled_task"}
    migration_component = next(item for item in contract["components"] if item["name"] == "migration")
    assert migration_component["automatic_on_failed_release"] == "only for reversible_without_data_loss steps"
    graph = migration.alembic_graph(Path(sys.executable), ROOT / "backend")
    assert migration.verify_contract_covers_graph(contract, graph) == {"classified": 17, "graph_revisions": 17}
    closure = next(item for item in contract["migrations"] if item["revision"] == "0006_operational_closure")
    assert closure["classification"] == "destructive_after_data"
    assert closure["automatic_downgrade_allowed"] is False
    assert closure["rollback_strategy"] == "restore_validated_pre_release_backup_or_roll_forward"


def test_rollback_contract_blocks_an_unclassified_migration():
    graph = graph_until("0016_operational_command_center")
    graph["revisions"].insert(0, {"revision": "example", "down_revisions": ["0016_operational_command_center"],
                                  "dependencies": [], "branch_labels": [], "path": "backend/alembic/versions/example.py"})
    graph["heads"] = ["example"]
    with pytest.raises(ControlPlaneError) as caught:
        migration.verify_contract_covers_graph(migration.load_rollback_contract(), graph)
    assert caught.value.code == "ROLLBACK_CONTRACT_INCOMPLETE"
    assert "unclassified:example" in caught.value.facts["problems"]


def test_release_tooling_never_rewrites_history_registers_tasks_or_drops_databases():
    sources = {path: path.read_text(encoding="utf-8").lower()
               for path in RELEASE.rglob("*") if path.suffix in {".py", ".ps1", ".json"}}
    combined = "\n".join(sources.values())
    for forbidden in ("git reset", "git rebase", "git checkout", "git switch", "git push", "git commit",
                      "git tag", "register-scheduledtask", "unregister-scheduledtask", "drop database",
                      "pg_terminate_backend", "--force"):
        assert forbidden not in combined, forbidden
    alembic_callers = [path.name for path, text in sources.items() if '"-m", "alembic"' in text]
    assert alembic_callers == ["migration.py"]
    controller = sources[RELEASE / "controlplane" / "controller.py"]
    assert controller.count('["upgrade", target]') == 1
    # Downgrade is reachable only behind the reversible-only decision (case 2).
    for fragment in ('["downgrade", target_head]', '["downgrade", state.data["db_revision_before"]]'):
        index = controller.index(fragment)
        assert 'decision["case"] == 2' in controller[max(0, index - 900):index]
    dropdb = [path.name for path, text in sources.items() if '"dropdb"' in text]
    assert dropdb == ["backup.py"]
