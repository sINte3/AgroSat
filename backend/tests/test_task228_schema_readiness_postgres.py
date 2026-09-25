"""TASK_228: readiness against a real, migrated PostgreSQL database.

Every request goes through the real ``/health/ready`` route, the real
``services.health`` code and a real SQLAlchemy engine. The route's engine is
pointed at the isolated database and the optional Redis probe is replaced;
nothing else is. The database is migrated with ``alembic upgrade head``
beforehand, so its ``alembic_version`` is exactly what a correctly deployed
release sees.

Database-side states are produced the way ``alembic stamp`` produces them: the
``alembic_version`` row is rewritten and always restored to the head
afterwards. Code-side states (a release ahead of, rolled back behind, or
branched from the database) resolve real, edited copies of the shipped
migration graph.

The central mismatch tests (``..._one_revision_behind_...`` and
``..._unknown_revision_...``) use only entry points that exist on 4cd8ea7, so
they run unchanged against the base and fail there with HTTP 200. Tests that
name a code-side graph need the TASK_228 resolver and say so.

The suite is skipped unless ``AGROSAT_TEST_DATABASE_URL`` is set, and it
refuses any database whose name does not start with ``agrosat_h0a``. The
production database is named ``agrosat`` and is rejected before any statement
runs.

    createdb agrosat_h0a_task228
    psql -d agrosat_h0a_task228 -c 'CREATE EXTENSION postgis;'
    DATABASE_URL=postgresql://.../agrosat_h0a_task228 python -m alembic upgrade head
    AGROSAT_TEST_DATABASE_URL=postgresql://.../agrosat_h0a_task228 \\
        python -m pytest tests/test_task228_schema_readiness_postgres.py
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit
import uuid

from task228_support import (
    migration_graph_copy,
    parent_of,
    production_status,
    shipped_head,
)


ISOLATED_PREFIX = "agrosat_h0a"
PRODUCTION_DATABASE = "agrosat"
# Shortly after the production-shaped status fixture finished (01:27:42Z).
NOW = datetime(2026, 9, 25, 2, 0, tzinfo=timezone.utc)


def _isolated_database_url() -> str | None:
    """Return the test database URL, refusing any non-isolated target."""
    url = (os.environ.get("AGROSAT_TEST_DATABASE_URL") or "").strip()
    if not url:
        return None
    name = urlsplit(url).path.lstrip("/")
    if name == PRODUCTION_DATABASE or not name.startswith(ISOLATED_PREFIX):
        raise RuntimeError(
            "refusing a non-isolated database target; the name must start with "
            f"{ISOLATED_PREFIX!r}"
        )
    return url


DATABASE_URL = _isolated_database_url()


@unittest.skipIf(DATABASE_URL is None, "AGROSAT_TEST_DATABASE_URL is not set")
class SchemaReadinessPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sqlalchemy import create_engine

        cls.engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=2)
        cls.head = shipped_head()
        cls.parent = parent_of(cls.head)
        stamped = cls._revisions()
        if stamped != [cls.head]:
            raise AssertionError(
                "the isolated database must be at the shipped head "
                f"{cls.head!r} (run alembic upgrade head); found {stamped!r}"
            )

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    # -- helpers ---------------------------------------------------------------

    @classmethod
    def _revisions(cls) -> list[str]:
        from sqlalchemy import text

        with cls.engine.connect() as connection:
            return list(
                connection.execute(
                    text("SELECT version_num FROM alembic_version ORDER BY 1")
                ).scalars()
            )

    @contextmanager
    def stamped(self, *revisions: str):
        """Rewrite ``alembic_version`` as ``alembic stamp`` would; restore the head."""
        from sqlalchemy import text

        try:
            with self.engine.begin() as connection:
                connection.execute(text("DELETE FROM alembic_version"))
                for revision in revisions:
                    connection.execute(
                        text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                        {"revision": revision},
                    )
            yield
        finally:
            with self.engine.begin() as connection:
                connection.execute(text("DELETE FROM alembic_version"))
                connection.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                    {"revision": self.head},
                )
            self.assertEqual(self._revisions(), [self.head])

    def ready(self, *, engine=None, cache=lambda: True, collector_directory="", code_head=None):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from api.health import router
        from config import settings

        app = FastAPI()
        app.include_router(router)
        with ExitStack() as stack:
            stack.enter_context(patch("database.engine", engine or self.engine))
            stack.enter_context(patch("services.cache.cache_probe", cache))
            stack.enter_context(
                patch.object(settings, "collector_status_directory", collector_directory)
            )
            stack.enter_context(patch("services.health.utc_now", lambda: NOW))
            if code_head is not None:
                # TASK_228 only: the code-side graph under test.
                stack.enter_context(
                    patch("services.health.expected_migration_head", lambda: code_head)
                )
            response = TestClient(app).get("/health/ready")
        return response, response.json()

    def code_graph(self, **edits):
        from services.migration_head import resolve_migration_head

        directory = tempfile.TemporaryDirectory(prefix="task228_graph_")
        self.addCleanup(directory.cleanup)
        return resolve_migration_head(migration_graph_copy(Path(directory.name), **edits))

    # -- 11. match ----------------------------------------------------------------

    def test_database_at_the_code_head_is_ready(self):
        response, body = self.ready()

        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(body["status"], "ready")
        self.assertEqual(body["components"]["database"]["status"], "ready")
        self.assertEqual(body["components"]["database"]["migration_revision"], self.head)

    def test_ready_database_reports_the_code_head_it_was_checked_against(self):
        response, body = self.ready()

        database = body["components"]["database"]
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(database["expected_migration_revision"], self.head)
        self.assertIs(database["revision_match"], True)
        self.assertIsNone(database["reason"])
        self.assertGreaterEqual(database["latency_ms"], 0)

    # -- 12/13. mismatch, database side ---------------------------------------------

    def test_database_one_revision_behind_is_not_ready(self):
        with self.stamped(self.parent):
            response, body = self.ready()

        self.assertEqual(response.status_code, 503, body)
        database = body["components"]["database"]
        self.assertEqual(body["status"], "not_ready")
        self.assertEqual(database["status"], "schema_mismatch")
        self.assertIs(database["revision_match"], False)
        self.assertEqual(database["migration_revision"], self.parent)
        self.assertEqual(database["expected_migration_revision"], self.head)
        self.assertEqual(database["reason"], "database_behind_code")

    def test_database_at_an_unknown_revision_is_not_ready(self):
        with self.stamped("0017_task228_not_shipped"):
            response, body = self.ready()

        self.assertEqual(response.status_code, 503, body)
        database = body["components"]["database"]
        self.assertEqual(database["status"], "schema_mismatch")
        self.assertEqual(database["migration_revision"], "0017_task228_not_shipped")
        self.assertEqual(database["expected_migration_revision"], self.head)
        self.assertEqual(database["reason"], "database_revision_unknown_to_code")

    # -- 14. more than one database revision ----------------------------------------

    def test_multiple_database_revisions_fail_closed(self):
        with self.stamped(self.head, self.parent):
            response, body = self.ready()

        self.assertEqual(response.status_code, 503, body)
        database = body["components"]["database"]
        self.assertEqual(database["status"], "schema_mismatch")
        self.assertEqual(database["migration_revision"], "unknown")
        self.assertEqual(database["reason"], "database_revision_multiple")

    def test_an_unstamped_database_fails_closed(self):
        with self.stamped():
            response, body = self.ready()

        self.assertEqual(response.status_code, 503, body)
        self.assertEqual(body["components"]["database"]["status"], "schema_mismatch")
        self.assertEqual(body["components"]["database"]["reason"], "database_revision_missing")

    # -- 15. unreachable ------------------------------------------------------------

    def test_an_unreachable_database_is_unavailable_and_sanitized(self):
        from sqlalchemy import create_engine

        parts = urlsplit(DATABASE_URL)
        absent = f"{ISOLATED_PREFIX}_task228_absent_{uuid.uuid4().hex[:8]}"
        engine = create_engine(
            urlunsplit((parts.scheme, parts.netloc, "/" + absent, "", "")),
            pool_pre_ping=True,
        )
        try:
            response, body = self.ready(engine=engine)
        finally:
            engine.dispose()

        self.assertEqual(response.status_code, 503, body)
        self.assertEqual(body["components"]["database"]["status"], "unavailable")
        self.assertEqual(body["components"]["database"]["migration_revision"], "unknown")
        serialized = json.dumps(body)
        for leaked in (absent, parts.hostname, parts.username or "postgres", "password"):
            self.assertNotIn(leaked, serialized)

    # -- code-side states (TASK_228 resolver) ----------------------------------------

    def test_code_one_revision_ahead_of_the_database_is_not_ready(self):
        ahead = self.code_graph(add=[("0017_task228_release_ahead", self.head)])

        response, body = self.ready(code_head=ahead)

        self.assertEqual(response.status_code, 503, body)
        database = body["components"]["database"]
        self.assertEqual(database["status"], "schema_mismatch")
        self.assertEqual(database["migration_revision"], self.head)
        self.assertEqual(database["expected_migration_revision"], "0017_task228_release_ahead")
        self.assertEqual(database["reason"], "database_behind_code")

    def test_code_rolled_back_behind_the_database_is_not_ready(self):
        rolled_back = self.code_graph(drop=[self.head])

        response, body = self.ready(code_head=rolled_back)

        self.assertEqual(response.status_code, 503, body)
        database = body["components"]["database"]
        self.assertEqual(database["status"], "schema_mismatch")
        self.assertEqual(database["expected_migration_revision"], self.parent)
        self.assertEqual(database["reason"], "database_revision_unknown_to_code")

    # -- 16/17. the code's own graph is unusable -------------------------------------

    def test_a_branched_code_graph_fails_closed_against_a_ready_database(self):
        branched = self.code_graph(add=[("0016_task228_branch", self.parent)])

        response, body = self.ready(code_head=branched)

        self.assertEqual(response.status_code, 503, body)
        database = body["components"]["database"]
        self.assertEqual(database["status"], "schema_unverified")
        self.assertEqual(database["reason"], "code_head_multiple")
        self.assertEqual(database["migration_revision"], self.head)
        self.assertEqual(database["expected_migration_revision"], "unknown")

    def test_an_unresolvable_code_graph_fails_closed_against_a_ready_database(self):
        from services.migration_head import resolve_migration_head

        with tempfile.TemporaryDirectory(prefix="task228_private_") as directory:
            unreadable = resolve_migration_head(Path(directory) / "alembic.ini")
            response, body = self.ready(code_head=unreadable)

        self.assertEqual(response.status_code, 503, body)
        self.assertEqual(body["components"]["database"]["status"], "schema_unverified")
        self.assertEqual(body["components"]["database"]["reason"], "code_head_unreadable")
        self.assertNotIn("task228_private_", json.dumps(body))

    # -- 18. optional cache ------------------------------------------------------------

    def test_cache_outage_keeps_a_matching_database_ready(self):
        def redis_down():
            raise ConnectionError("redis://:secret@localhost:6379/0 refused")

        for cache in (lambda: False, redis_down):
            response, body = self.ready(cache=cache)
            self.assertEqual(response.status_code, 200, body)
            self.assertEqual(body["components"]["cache"]["status"], "unavailable")
            self.assertNotIn("secret", json.dumps(body))

    # -- the collector, read through the same response ---------------------------------

    def _collector_directory(self, payload) -> str:
        from scripts import collect_satellite as collector

        directory = tempfile.TemporaryDirectory(prefix="task228_status_")
        self.addCleanup(directory.cleanup)
        collector.atomic_json(Path(directory.name) / "collector_latest_status.json", payload)
        return directory.name

    def test_production_collector_status_is_published_by_readiness(self):
        response, body = self.ready(collector_directory=self._collector_directory(production_status()))

        self.assertEqual(response.status_code, 200, body)
        collector = body["components"]["collector"]
        self.assertEqual(collector["status"], "succeeded")
        self.assertIs(collector["required_for_api_readiness"], False)
        self.assertEqual(
            [item["provider"] for item in collector["latest"]["providers"]],
            ["ndvi", "multi"],
        )
        self.assertEqual(
            [item["batch_count"] for item in collector["latest"]["providers"]],
            [11, 11],
        )

    def test_collector_failure_never_makes_the_api_unready(self):
        failed = production_status()
        failed.update(status="failed", exit_code=4, failure_category="operational")
        failed["providers"] = failed["providers"][:3]
        failed["providers"][-1] = {
            "provider": "ndvi",
            "exit_code": 4,
            "timed_out": False,
            "counters": None,
        }
        malformed = {"run_id": "not-a-run", "providers": "everything"}

        for payload in (failed, malformed):
            response, body = self.ready(collector_directory=self._collector_directory(payload))
            self.assertEqual(response.status_code, 200, body)
            self.assertEqual(body["status"], "ready")
            self.assertIs(body["components"]["collector"]["required_for_api_readiness"], False)


if __name__ == "__main__":
    unittest.main()
