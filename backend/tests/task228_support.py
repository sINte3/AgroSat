"""Shared, database-free helpers for the TASK_228 readiness suites.

Not collected by pytest (no ``test_`` prefix). Nothing here imports the code
under test, so the same helpers drive the suites against the base commit and
against the candidate; that is how the discriminating tests are shown to fail
on 4cd8ea7 for the reason they claim.

* ``production_status()`` returns the sanitized collector status fixture. It
  is the status file the canonical collector wrote in production at the end of
  the 2026-09-25 01:00 UTC (06:00 local) cycle on release 4cd8ea7, with its run
  id replaced by a synthetic one. Nothing else was altered: 275 active fields
  at ``--batch-size 25`` gave 11 NDVI and 11 multi-index batch entries.
* ``shipped_graph()`` reads the migration graph statically from the migration
  files, independently of Alembic, so a test can cross-check the runtime
  resolver instead of trusting it.
* ``migration_graph_copy()`` builds an edited copy of the shipped graph that
  Alembic then resolves for real: a code release one revision ahead of the
  database, one rolled back behind it, a branched graph, an empty one.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import shutil
from typing import Any, Iterable


BACKEND = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND / "alembic.ini"
VERSIONS = BACKEND / "alembic" / "versions"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PRODUCTION_STATUS_FIXTURE = (
    FIXTURES / "task228_collector_latest_status_production_shape.json"
)
COUNTER_FIELDS = (
    "success_count",
    "failure_count",
    "inserted_count",
    "skipped_existing_count",
    "quality_blocked_count",
    "timeout_count",
)
SYNTHETIC_MIGRATION = '''"""TASK_228 synthetic revision for readiness tests."""

revision = {revision!r}
down_revision = {down_revision!r}
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
'''


def production_status() -> dict[str, Any]:
    return json.loads(PRODUCTION_STATUS_FIXTURE.read_text(encoding="utf-8"))


def counters(**values: int) -> dict[str, int]:
    result = dict.fromkeys(COUNTER_FIELDS, 0)
    result.update(values)
    return result


def _module_assignments(path: Path) -> dict[str, Any]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    values: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if isinstance(target, ast.Name) and target.id in {
            "revision",
            "down_revision",
        }:
            values[target.id] = ast.literal_eval(value)
    return values


def shipped_graph() -> dict[str, tuple[str, ...]]:
    """Map every shipped revision to its parents, read without Alembic."""
    graph: dict[str, tuple[str, ...]] = {}
    for path in sorted(VERSIONS.glob("*.py")):
        values = _module_assignments(path)
        down = values.get("down_revision")
        if down is None:
            parents: tuple[str, ...] = ()
        elif isinstance(down, str):
            parents = (down,)
        else:
            parents = tuple(down)
        graph[values["revision"]] = parents
    return graph


def shipped_head() -> str:
    graph = shipped_graph()
    referenced = {parent for parents in graph.values() for parent in parents}
    heads = sorted(set(graph) - referenced)
    if len(heads) != 1:
        raise AssertionError(f"the shipped graph must have one head, found {heads}")
    return heads[0]


def parent_of(revision: str) -> str:
    parents = shipped_graph()[revision]
    if len(parents) != 1:
        raise AssertionError(f"{revision} must have exactly one parent")
    return parents[0]


def revision_file(revision: str) -> str:
    for path in sorted(VERSIONS.glob("*.py")):
        if _module_assignments(path).get("revision") == revision:
            return path.name
    raise AssertionError(f"no migration file declares {revision}")


def migration_graph_copy(
    root: Path,
    *,
    drop: Iterable[str] = (),
    add: Iterable[tuple[str, str | None]] = (),
    include_shipped: bool = True,
) -> Path:
    """Copy the shipped Alembic graph under ``root``, edit it, return its ini.

    ``drop`` names revisions whose files are left out; ``add`` appends
    synthetic ``(revision, down_revision)`` migrations. The copied
    ``alembic.ini`` is the shipped one, so Alembic resolves the copy exactly as
    it resolves the release.
    """
    versions = root / "alembic" / "versions"
    versions.mkdir(parents=True)
    shutil.copyfile(ALEMBIC_INI, root / "alembic.ini")
    dropped = {revision_file(revision) for revision in drop}
    if include_shipped:
        for source in sorted(VERSIONS.glob("*.py")):
            if source.name not in dropped:
                shutil.copyfile(source, versions / source.name)
    for index, (revision, down_revision) in enumerate(add, start=1):
        (versions / f"task228_synthetic_{index}.py").write_text(
            SYNTHETIC_MIGRATION.format(
                revision=revision,
                down_revision=down_revision,
            ),
            encoding="utf-8",
        )
    return root / "alembic.ini"
