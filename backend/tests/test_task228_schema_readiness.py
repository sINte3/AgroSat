"""TASK_228 / Parts B-D and G: readiness proves the schema this code requires.

On 4cd8ea7 ``database_readiness`` proved only that ``SELECT 1`` worked and that
``alembic_version`` held one value. A release could run against the wrong
schema and still answer 200.

The expected revision now comes from one resolver,
``services.migration_head``, which reads the Alembic migration graph shipped
with the running code through Alembic's own ``ScriptDirectory`` and caches
the result for the process. Nothing here derives it from the release SHA.

The graphs these tests resolve are real: copies of the shipped graph, edited
where a test needs a code release that is ahead of, behind, or branched from
the database. PostgreSQL-backed proof of the same states lives in
``test_task228_schema_readiness_postgres.py``.
"""

from __future__ import annotations

import ast
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import threading
import time
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from api.health import router
from config import settings
from services import health, migration_head
from services.migration_head import MigrationHead, resolve_migration_head
from task228_support import (
    ALEMBIC_INI,
    BACKEND,
    migration_graph_copy,
    parent_of,
    shipped_graph,
    shipped_head,
)


HEAD = shipped_head()
PARENT = parent_of(HEAD)
SHIPPED = MigrationHead(HEAD, None, frozenset(shipped_graph()))


class Rows:
    def __init__(self, values):
        self.values = values

    def scalar_one(self):
        return self.values[0]

    def scalars(self):
        return iter(self.values)


class Connection:
    def __init__(self, revisions, fail_on=None):
        self.revisions = revisions
        self.fail_on = fail_on
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("server closed the connection: postgresql://u:secret@db-host/agrosat")
        return Rows([1]) if sql == "SELECT 1" else Rows(list(self.revisions))


def engine_at(*revisions, fail_on=None):
    engine = Mock()
    engine.connection = Connection(revisions, fail_on=fail_on)
    engine.connect.return_value = engine.connection
    return engine


def unreachable_engine():
    engine = Mock()
    engine.connect.side_effect = RuntimeError(
        'could not connect to "db-host" as user "postgres" password=hunter2'
    )
    return engine


def database(engine, head=SHIPPED):
    return health.database_readiness(engine, migration_head=head)


# -- the resolver ---------------------------------------------------------------


def test_resolver_reads_the_single_head_of_the_shipped_graph():
    resolved = resolve_migration_head()

    assert resolved.resolved and resolved.problem is None
    # Cross-checked against a static read of the migration files.
    assert resolved.revision == HEAD
    assert resolved.known_revisions == frozenset(shipped_graph())


def test_default_resolver_reads_the_graph_shipped_next_to_the_code():
    assert Path(migration_head.ALEMBIC_INI).resolve() == ALEMBIC_INI.resolve()


def test_resolver_follows_the_graph_it_is_given(tmp_path):
    ahead = resolve_migration_head(
        migration_graph_copy(tmp_path / "ahead", add=[("0017_task228_probe", HEAD)])
    )
    behind = resolve_migration_head(migration_graph_copy(tmp_path / "behind", drop=[HEAD]))

    assert ahead.revision == "0017_task228_probe"
    assert HEAD in ahead.known_revisions
    assert behind.revision == PARENT
    assert HEAD not in behind.known_revisions


def test_multiple_code_heads_fail_closed(tmp_path):
    ini = migration_graph_copy(tmp_path, add=[("0016_task228_branch", PARENT)])

    assert resolve_migration_head(ini) == MigrationHead(None, "multiple_heads")


@pytest.mark.parametrize("versions_directory", ["empty", "absent"])
def test_a_graph_without_revisions_fails_closed(tmp_path, versions_directory):
    ini = migration_graph_copy(tmp_path, include_shipped=False)
    if versions_directory == "absent":
        # Alembic walks a missing versions directory as an empty graph.
        (tmp_path / "alembic" / "versions").rmdir()

    assert resolve_migration_head(ini) == MigrationHead(None, "no_head")


def _missing_ini(root):
    return root / "absent" / "alembic.ini"


def _ini_without_script_location(root):
    root.mkdir()
    (root / "alembic.ini").write_text("[alembic]\nsqlalchemy.url = x\n", encoding="utf-8")
    return root / "alembic.ini"


def _orphan_revision(root):
    return migration_graph_copy(root, add=[("0017_task228_orphan", "0000_not_shipped")])


def _syntax_error(root):
    ini = migration_graph_copy(root)
    (root / "alembic" / "versions" / "0017_broken.py").write_text(
        "revision = '0017_broken'\ndown_revision = (\n", encoding="utf-8"
    )
    return ini


def _missing_script_directory(root):
    ini = migration_graph_copy(root, include_shipped=False)
    (root / "alembic" / "versions").rmdir()
    (root / "alembic").rmdir()
    return ini


UNREADABLE_GRAPHS = {
    "missing_ini": _missing_ini,
    "ini_without_script_location": _ini_without_script_location,
    "orphan_revision": _orphan_revision,
    "syntax_error": _syntax_error,
    "missing_script_directory": _missing_script_directory,
}


@pytest.mark.parametrize("name", sorted(UNREADABLE_GRAPHS))
@pytest.mark.filterwarnings("ignore:Revision .* is not present:UserWarning")
def test_an_unreadable_graph_fails_closed(tmp_path, name):
    ini = UNREADABLE_GRAPHS[name](tmp_path / "graph")

    assert resolve_migration_head(ini) == MigrationHead(None, "unreadable")


def test_resolution_leaves_the_import_path_alone():
    # alembic.ini carries prepend_sys_path = . for the CLI; a health probe
    # must not rewrite a web process's import path.
    before = list(sys.path)

    resolve_migration_head()

    assert sys.path == before


# -- caching (Part G) -------------------------------------------------------------


def _counting(monkeypatch, result, *, delay=0.0):
    calls = []

    def resolver(*args, **kwargs):
        calls.append(1)
        time.sleep(delay)
        return result

    monkeypatch.setattr(migration_head, "_cache", None)
    monkeypatch.setattr(migration_head, "resolve_migration_head", resolver)
    return calls


def test_the_shipped_head_is_resolved_once_per_process(monkeypatch):
    calls = _counting(monkeypatch, SHIPPED)

    results = {migration_head.expected_migration_head() for _ in range(200)}

    assert results == {SHIPPED}
    assert len(calls) == 1


def test_concurrent_first_probes_resolve_once(monkeypatch):
    calls = _counting(monkeypatch, SHIPPED, delay=0.05)
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(migration_head.expected_migration_head()))
        for _ in range(16)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == [SHIPPED] * 16
    assert len(calls) == 1


def test_an_unresolved_head_is_retried_only_after_the_backoff(monkeypatch):
    failure = MigrationHead(None, "multiple_heads")
    calls = _counting(monkeypatch, failure)
    now = [1_000.0]

    def probe():
        return migration_head.expected_migration_head(clock=lambda: now[0])

    for _ in range(50):
        assert probe() == failure
    assert len(calls) == 1

    now[0] += migration_head.UNRESOLVED_RETRY_SECONDS - 1
    probe()
    assert len(calls) == 1

    now[0] += 2
    probe()
    assert len(calls) == 2


# -- database component states (Part C) -------------------------------------------


def test_matching_revision_is_ready_with_two_bounded_statements():
    engine = engine_at(HEAD)

    result = database(engine)

    assert result["status"] == "ready"
    assert result["revision_match"] is True
    assert result["migration_revision"] == HEAD
    assert result["expected_migration_revision"] == HEAD
    assert result["reason"] is None
    assert isinstance(result["latency_ms"], float)
    assert engine.connection.statements == [
        "SELECT 1",
        "SELECT version_num FROM alembic_version LIMIT 2",
    ]


def test_database_behind_the_code_is_a_schema_mismatch():
    result = database(engine_at(PARENT))

    assert result["status"] == "schema_mismatch"
    assert result["revision_match"] is False
    assert result["migration_revision"] == PARENT
    assert result["expected_migration_revision"] == HEAD
    assert result["reason"] == "database_behind_code"


def test_database_revision_unknown_to_the_code_is_a_schema_mismatch():
    result = database(engine_at("0017_from_a_newer_release"))

    assert result["status"] == "schema_mismatch"
    assert result["migration_revision"] == "0017_from_a_newer_release"
    assert result["reason"] == "database_revision_unknown_to_code"


@pytest.mark.parametrize(
    ("revisions", "reason"),
    [((HEAD, PARENT), "database_revision_multiple"), ((), "database_revision_missing")],
)
def test_a_non_singular_database_revision_fails_closed(revisions, reason):
    result = database(engine_at(*revisions))

    assert result["status"] == "schema_mismatch"
    assert result["revision_match"] is False
    assert result["migration_revision"] == "unknown"
    assert result["reason"] == reason


def test_an_unsafe_database_revision_is_never_published():
    result = database(engine_at("0016_operational_command_center; DROP TABLE users"))

    assert result["status"] == "schema_mismatch"
    assert result["migration_revision"] == "unknown"


@pytest.mark.parametrize(
    "engine_factory",
    [unreachable_engine, lambda: engine_at(HEAD, fail_on="alembic_version")],
)
def test_an_unavailable_database_is_sanitized(engine_factory):
    result = database(engine_factory())

    assert result["status"] == "unavailable"
    assert result["revision_match"] is False
    assert result["migration_revision"] == "unknown"
    assert result["expected_migration_revision"] == HEAD
    assert result["reason"] == "database_unreachable"
    serialized = json.dumps(result)
    for leaked in ("hunter2", "secret", "db-host", "postgres"):
        assert leaked not in serialized


@pytest.mark.parametrize(
    ("problem", "reason"),
    [
        ("multiple_heads", "code_head_multiple"),
        ("no_head", "code_head_missing"),
        ("unreadable", "code_head_unreadable"),
    ],
)
def test_an_unresolved_code_head_fails_closed(problem, reason):
    result = database(engine_at(HEAD), head=MigrationHead(None, problem))

    assert result["status"] == "schema_unverified"
    assert result["revision_match"] is False
    assert result["migration_revision"] == HEAD
    assert result["expected_migration_revision"] == "unknown"
    assert result["reason"] == reason


def test_an_unreadable_graph_leaks_no_path_into_readiness(tmp_path):
    unreadable = resolve_migration_head(tmp_path / "private" / "alembic.ini")
    snapshot = health.readiness_snapshot(
        engine=engine_at(HEAD),
        cache_check=lambda: True,
        collector_directory="",
        collector_stale_after_seconds=3600,
        migration_head=unreadable,
    )

    assert snapshot["status"] == "not_ready"
    serialized = json.dumps(snapshot)
    assert str(tmp_path) not in serialized
    assert "private" not in serialized
    assert "alembic.ini" not in serialized


# -- HTTP contract ------------------------------------------------------------------


def http(engine, *, head=SHIPPED, cache=lambda: True):
    app = FastAPI()
    app.include_router(router)
    with ExitStack() as stack:
        stack.enter_context(patch("database.engine", engine))
        stack.enter_context(patch("services.cache.cache_probe", cache))
        stack.enter_context(patch.object(settings, "collector_status_directory", ""))
        stack.enter_context(patch("services.health.expected_migration_head", lambda: head))
        client = TestClient(app)
        return client.get("/health/ready"), client.get("/health"), client.get("/health/live")


@pytest.mark.parametrize(
    ("engine_factory", "head", "code", "status"),
    [
        (lambda: engine_at(HEAD), SHIPPED, 200, "ready"),
        (lambda: engine_at(PARENT), SHIPPED, 503, "schema_mismatch"),
        (lambda: engine_at("0017_future"), SHIPPED, 503, "schema_mismatch"),
        (lambda: engine_at(HEAD, PARENT), SHIPPED, 503, "schema_mismatch"),
        (unreachable_engine, SHIPPED, 503, "unavailable"),
        (lambda: engine_at(HEAD), MigrationHead(None, "multiple_heads"), 503, "schema_unverified"),
        (lambda: engine_at(HEAD), MigrationHead(None, "unreadable"), 503, "schema_unverified"),
    ],
)
def test_readiness_http_status_follows_the_database_component(engine_factory, head, code, status):
    ready, compatibility, live = http(engine_factory(), head=head)

    assert ready.status_code == code
    assert ready.json()["components"]["database"]["status"] == status
    assert ready.json()["status"] == ("ready" if code == 200 else "not_ready")
    # The compatibility and liveness endpoints keep their contracts.
    assert compatibility.status_code == 200
    assert compatibility.json()["status"] == ("ok" if code == 200 else "degraded")
    assert live.status_code == 200 and live.json()["status"] == "alive"


@pytest.mark.parametrize("cache", [lambda: False, Mock(side_effect=ConnectionError("redis down"))])
def test_cache_outage_keeps_a_matching_database_ready(cache):
    ready, _, _ = http(engine_at(HEAD), cache=cache)

    assert ready.status_code == 200
    assert ready.json()["components"]["cache"] == {
        "status": "unavailable",
        "required_for_api_readiness": False,
        "source_of_truth": False,
    }


def test_liveness_touches_neither_database_nor_migration_graph():
    with patch("services.health.database_readiness") as database_probe, patch(
        "services.health.expected_migration_head"
    ) as head_probe:
        snapshot = health.liveness_snapshot()

    assert snapshot["status"] == "alive"
    database_probe.assert_not_called()
    head_probe.assert_not_called()


# -- release identity (Part D) -------------------------------------------------------


STALE_RELEASES = (
    "66c1be8bab62832c0d94402b7a8f3ac7672fffe0",  # the release before 4cd8ea7
    "4cd8ea7240bbd2307488ad4871e504a672a812a6",
    "unknown",
    "",
    "release; DROP TABLE alembic_version",
)


@pytest.mark.parametrize("release", STALE_RELEASES)
def test_release_revision_never_moves_the_expected_head(monkeypatch, release):
    monkeypatch.setattr(settings, "release_revision", release)
    monkeypatch.setattr(migration_head, "_cache", None)  # resolve for real

    matching = health.readiness_snapshot(
        engine=engine_at(HEAD),
        cache_check=lambda: True,
        collector_directory="",
        collector_stale_after_seconds=3600,
    )
    behind = health.readiness_snapshot(
        engine=engine_at(PARENT),
        cache_check=lambda: True,
        collector_directory="",
        collector_stale_after_seconds=3600,
    )

    assert matching["components"]["database"]["expected_migration_revision"] == HEAD
    assert behind["components"]["database"]["expected_migration_revision"] == HEAD
    assert matching["status"] == "ready"
    # A release identity that looks right never excuses a schema that is not.
    assert behind["status"] == "not_ready"
    assert matching["release_revision"] == health.safe_revision(release)


@pytest.mark.parametrize(
    ("release", "published"),
    [
        ("4cd8ea7240bbd2307488ad4871e504a672a812a6", "4cd8ea7240bbd2307488ad4871e504a672a812a6"),
        ("..\\..\\Windows\\System32", "unknown"),
        ("C:\\AgroSat_releases\\PROGRAM_R3\\x", "unknown"),
        ("postgresql://postgres:pw@localhost/agrosat", "unknown"),
        ("a" * 129, "unknown"),
        ("", "unknown"),
    ],
)
def test_release_revision_stays_sanitized(monkeypatch, release, published):
    monkeypatch.setattr(settings, "release_revision", release)

    live = health.liveness_snapshot()
    ready = health.readiness_snapshot(
        engine=engine_at(HEAD),
        cache_check=lambda: True,
        collector_directory="",
        collector_stale_after_seconds=3600,
        migration_head=SHIPPED,
    )

    assert live["release_revision"] == published
    assert ready["release_revision"] == published


# -- web safety (Part G) ----------------------------------------------------------


HEALTH_SOURCES = (
    BACKEND / "services" / "health.py",
    BACKEND / "services" / "migration_head.py",
    BACKEND / "api" / "health.py",
)
FORBIDDEN_MODULES = {"subprocess", "git", "alembic.command", "multiprocessing", "sched"}
FORBIDDEN_CALLS = {"system", "popen", "Popen", "run", "upgrade", "downgrade", "stamp", "create_all"}


@pytest.mark.parametrize("path", HEALTH_SOURCES, ids=lambda path: path.name)
def test_health_code_spawns_nothing_and_migrates_nothing(path):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not {alias.name for alias in node.names} & FORBIDDEN_MODULES
        elif isinstance(node, ast.ImportFrom):
            assert node.module not in FORBIDDEN_MODULES
            assert not {f"{node.module}.{alias.name}" for alias in node.names} & FORBIDDEN_MODULES
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            assert name not in FORBIDDEN_CALLS, f"{path.name} calls {name}()"
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            upper = node.value.upper()
            assert not any(
                upper.lstrip().startswith(keyword)
                for keyword in ("CREATE ", "ALTER ", "DROP ", "INSERT ", "UPDATE ", "DELETE ", "TRUNCATE ")
            ), node.value


@pytest.mark.parametrize("path", HEALTH_SOURCES, ids=lambda path: path.name)
def test_no_migration_revision_is_hardcoded(path):
    source = path.read_text(encoding="utf-8-sig")
    for revision in shipped_graph():
        assert revision not in source, f"{path.name} hardcodes {revision}"
