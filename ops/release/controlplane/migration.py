"""Migration planning and the rollback classification guard.

The migration graph is read with Alembic's own ``ScriptDirectory`` inside the
release's interpreter, from the release's migration files: the same graph
``alembic upgrade`` would walk. No regex, no Git history.

Plan rules (TASK_230 Part I):

* candidate graph without exactly one head -> blocked
* database without a revision, or with several rows -> blocked
* database revision unknown to the candidate graph (ahead or foreign) -> blocked
* database at the candidate head -> ``noop``
* database behind the head -> ``upgrade``, with the exact ordered path; every
  step must carry a rollback classification (Part J)

Downgrade is permitted only when every applied step is classified
``reversible_without_data_loss`` (Part K). Nothing here runs a migration by
itself; ``run_alembic`` is called by the controller's gated steps only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any

from .common import ControlPlaneError, read_json

CLASSIFICATIONS = ("reversible_without_data_loss", "destructive_after_data", "restore_backup_or_roll_forward_only")
STRATEGIES = {
    "reversible_without_data_loss": "alembic_downgrade",
    "destructive_after_data": "restore_validated_pre_release_backup_or_roll_forward",
    "restore_backup_or_roll_forward_only": "restore_validated_pre_release_backup_or_roll_forward",
}
ROLLBACK_CONTRACT = Path(__file__).resolve().parents[1] / "rollback-contract.json"

GRAPH_SCRIPT = r"""
import json, sys
from pathlib import Path
from alembic.config import Config
from alembic.script import ScriptDirectory
backend = Path(sys.argv[1]).resolve()
config = Config(str(backend / "alembic.ini"))
config.set_main_option("script_location", str(backend / "alembic"))
config.set_main_option("prepend_sys_path", "")
script = ScriptDirectory.from_config(config)
def names(value):
    if value is None:
        return []
    return [value] if isinstance(value, str) else sorted(value)
revisions = [{
    "revision": item.revision,
    "down_revisions": names(item.down_revision),
    "dependencies": names(item.dependencies),
    "branch_labels": sorted(item.branch_labels or []),
    "path": Path(item.path).resolve().relative_to(backend.parent).as_posix(),
} for item in script.walk_revisions()]
print(json.dumps({"heads": sorted(script.get_heads()), "revisions": revisions}))
"""


def alembic_graph(python: Path, backend_directory: Path) -> dict[str, Any]:
    """The migration graph shipped in ``backend_directory``, read by its own Alembic."""
    result = subprocess.run([str(python), "-B", "-c", GRAPH_SCRIPT, str(backend_directory)],
                            capture_output=True, text=True, cwd=str(backend_directory), timeout=300,
                            env={key: value for key, value in os.environ.items()
                                 if key.upper() not in {"DATABASE_URL", "PYTHONPATH"}})
    if result.returncode != 0:
        raise ControlPlaneError("ALEMBIC_GRAPH_UNREADABLE", " ".join(result.stderr.split())[-400:])
    try:
        graph = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise ControlPlaneError("ALEMBIC_GRAPH_UNREADABLE", "no graph output") from None
    return graph


def single_head(graph: dict[str, Any]) -> str:
    heads = graph.get("heads") or []
    if len(heads) != 1:
        raise ControlPlaneError("ALEMBIC_HEAD_COUNT_REJECTED", f"{len(heads)} heads", heads=heads)
    return heads[0]


def ordered_revisions(graph: dict[str, Any]) -> list[str]:
    """Base-to-head order of a linear graph (fails closed on branches or merges)."""
    head = single_head(graph)
    by_revision = {item["revision"]: item for item in graph["revisions"]}
    chain, current = [], head
    while current is not None:
        item = by_revision.get(current)
        if item is None:
            raise ControlPlaneError("ALEMBIC_GRAPH_BROKEN", current)
        chain.append(current)
        downs = item["down_revisions"]
        if len(downs) > 1:
            raise ControlPlaneError("ALEMBIC_GRAPH_NOT_LINEAR", current)
        current = downs[0] if downs else None
    if len(chain) != len(by_revision):
        raise ControlPlaneError("ALEMBIC_GRAPH_NOT_LINEAR", "revisions outside the head's chain")
    return list(reversed(chain))


def load_rollback_contract(path: Path = ROLLBACK_CONTRACT) -> dict[str, Any]:
    contract = read_json(path, "ROLLBACK_CONTRACT_UNREADABLE")
    if not isinstance(contract, dict) or contract.get("schema_version") != 2:
        raise ControlPlaneError("ROLLBACK_CONTRACT_SCHEMA_REJECTED")
    seen = set()
    for item in contract.get("migrations", []):
        revision = item.get("revision")
        classification = item.get("classification")
        if revision in seen:
            raise ControlPlaneError("ROLLBACK_CONTRACT_DUPLICATE", str(revision))
        seen.add(revision)
        if classification not in CLASSIFICATIONS:
            raise ControlPlaneError("ROLLBACK_CONTRACT_CLASSIFICATION_REJECTED", str(revision))
        if item.get("automatic_downgrade_allowed") is not (classification == "reversible_without_data_loss"):
            raise ControlPlaneError("ROLLBACK_CONTRACT_DOWNGRADE_FLAG_REJECTED", str(revision))
        if item.get("rollback_strategy") != STRATEGIES[classification]:
            raise ControlPlaneError("ROLLBACK_CONTRACT_STRATEGY_REJECTED", str(revision))
    return contract


def verify_contract_covers_graph(contract: dict[str, Any], graph: dict[str, Any], *, exact: bool = True) -> dict[str, Any]:
    """Every shipped migration is classified, with matching path and parent.

    ``exact`` (the repository check) also refuses classifications for
    revisions the graph does not contain; a release of an older candidate
    passes ``exact=False``, since the tooling may already classify newer ones.
    """
    classified = {item["revision"]: item for item in contract["migrations"]}
    problems = []
    for item in graph["revisions"]:
        entry = classified.get(item["revision"])
        if entry is None:
            problems.append(f"unclassified:{item['revision']}")
            continue
        if entry.get("path") != item["path"]:
            problems.append(f"path_mismatch:{item['revision']}")
        if sorted(entry.get("down_revisions", [])) != sorted(item["down_revisions"]):
            problems.append(f"parent_mismatch:{item['revision']}")
    if exact:
        extra = sorted(set(classified) - {item["revision"] for item in graph["revisions"]})
        problems.extend(f"not_in_graph:{revision}" for revision in extra)
    if problems:
        raise ControlPlaneError("ROLLBACK_CONTRACT_INCOMPLETE", f"{len(problems)} problems", problems=problems)
    return {"classified": len(classified), "graph_revisions": len(graph["revisions"])}


def plan_migration(graph: dict[str, Any], database_revisions: list[str], contract: dict[str, Any]) -> dict[str, Any]:
    """What the release must do to the database; never raises for a blocked plan."""
    def blocked(reason: str, **facts: Any) -> dict[str, Any]:
        return {"status": "blocked", "reason": reason, "db_revision_before": before, "target_revision": head,
                "upgrade_path": [], "steps": [], "automatic_downgrade_allowed": False, **facts}

    before = database_revisions[0] if len(database_revisions) == 1 else None
    heads = graph.get("heads") or []
    head = heads[0] if len(heads) == 1 else None
    if len(heads) != 1:
        return blocked("candidate_head_count", heads=heads)
    try:
        order = ordered_revisions(graph)
    except ControlPlaneError as error:
        return blocked(error.code.lower())
    if not database_revisions:
        return blocked("database_revision_missing")
    if len(database_revisions) > 1:
        return blocked("database_multiple_heads", database_revisions=database_revisions)
    if before not in order:
        return blocked("database_revision_unknown_to_candidate")
    if before == head:
        return {"status": "noop", "reason": "database_at_candidate_head", "db_revision_before": before,
                "target_revision": head, "upgrade_path": [], "steps": [], "automatic_downgrade_allowed": None,
                "rollback_classification": None}
    path = order[order.index(before) + 1:]
    classified = {item["revision"]: item for item in contract["migrations"]}
    steps = []
    for revision in path:
        entry = classified.get(revision)
        if entry is None:
            return blocked("migration_unclassified", unclassified=revision)
        steps.append({"revision": revision, "classification": entry["classification"],
                      "rollback_strategy": entry["rollback_strategy"], "data_at_risk": entry.get("data_at_risk", [])})
    worst = max((CLASSIFICATIONS.index(step["classification"]) for step in steps), default=0)
    return {
        "status": "upgrade", "reason": "database_behind_candidate", "db_revision_before": before,
        "target_revision": head, "upgrade_path": path, "steps": steps,
        "rollback_classification": CLASSIFICATIONS[worst],
        "automatic_downgrade_allowed": all(step["classification"] == "reversible_without_data_loss" for step in steps),
    }


def downgrade_decision(applied_steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Case 1/2/3 of the rollback contract for the steps a release applied."""
    if not applied_steps:
        return {"case": 1, "database_action": "none"}
    if all(step["classification"] == "reversible_without_data_loss" for step in applied_steps):
        return {"case": 2, "database_action": "alembic_downgrade"}
    return {"case": 3, "database_action": "restore_validated_pre_release_backup",
            "automatic_downgrade": "forbidden",
            "blocking_steps": [step["revision"] for step in applied_steps
                               if step["classification"] != "reversible_without_data_loss"]}


def run_alembic(python: Path, backend_directory: Path, env_file: Path, arguments: list[str],
                *, timeout: int = 3600) -> subprocess.CompletedProcess:
    """``alembic <arguments>`` from the release, reading credentials from ``env_file`` itself."""
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() not in {"DATABASE_URL", "PYTHONPATH", "PGPASSWORD"}}
    environment["AGROSAT_RUNTIME_ENV_FILE"] = str(env_file)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run([str(python), "-B", "-m", "alembic", *arguments], cwd=str(backend_directory),
                          env=environment, capture_output=True, text=True, timeout=timeout)
