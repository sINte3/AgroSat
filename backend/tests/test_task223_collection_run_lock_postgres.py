"""TASK_223: PostgreSQL proof that a collection run owns one backend.

``begin_apply_run`` takes a *session-scoped* ``pg_try_advisory_lock``. A
session-scoped advisory lock belongs to the PostgreSQL backend that took it,
not to the SQLAlchemy ``Session``. SQLAlchemy returns the connection to the
pool on ``commit()``, so on the original code the run keeps executing on
whatever connection the pool hands back next. Three things follow, and all
three are proved here against a real database:

* the lock ends up held by a backend the run no longer uses, and that backend
  is handed to unrelated callers while it still holds the lock;
* a second ``begin_apply_run`` that happens to be given that same pooled
  connection re-acquires the lock re-entrantly and reports no contention, so
  the single-writer guarantee the lock exists to provide is void;
* the run's own unlock runs on the wrong backend, so it releases nothing and
  the lock leaks for the lifetime of the pooled connection.

The suite is skipped unless ``AGROSAT_TEST_DATABASE_URL`` is set. It is
destructive within its own database, so it refuses any target whose database
name does not start with ``agrosat_h0a``. The production database is named
``agrosat`` and is rejected before any statement runs.

    createdb agrosat_h0a_task223
    psql -d agrosat_h0a_task223 -c 'CREATE EXTENSION postgis;'
    DATABASE_URL=postgresql://.../agrosat_h0a_task223 python -m alembic upgrade head
    AGROSAT_TEST_DATABASE_URL=postgresql://.../agrosat_h0a_task223 \
        python -m pytest tests/test_task223_collection_run_lock_postgres.py
"""

from __future__ import annotations

import os
import unittest
from urllib.parse import urlsplit


ISOLATED_PREFIX = "agrosat_h0a"
PRODUCTION_DATABASE = "agrosat"
RELEASE_COMMIT = "0" * 40


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
class CollectionRunLockBase(unittest.TestCase):
    """Real database, real pool, real advisory locks."""

    @classmethod
    def setUpClass(cls):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        # A small pool makes the hand-back deterministic: the connection a
        # session releases on commit is the one the next checkout receives.
        cls.engine = create_engine(DATABASE_URL, future=True, pool_size=5, max_overflow=5)
        cls.Session = sessionmaker(bind=cls.engine, autocommit=False, autoflush=False)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        from sqlalchemy import text

        from services import autonomous_monitoring

        self.monitoring = autonomous_monitoring
        self.key = autonomous_monitoring.ADVISORY_LOCK_KEY
        self._patched = {}
        # The service resolves its own database handles at module scope. Point
        # both at the isolated engine; `engine` is absent on the original code
        # and simply unused there.
        for name, value in (("SessionLocal", self.Session), ("engine", self.engine)):
            self._patched[name] = getattr(autonomous_monitoring, name, None)
            setattr(autonomous_monitoring, name, value)
        self.opened = []
        with self.engine.begin() as connection:
            connection.execute(text("DELETE FROM satellite_collection_runs"))
        self._release_all_advisory_locks()

    def tearDown(self):
        for run in getattr(self, "runs", []):
            self._discard(run)
        for session in self.opened:
            try:
                session.close()
            except Exception:
                pass
        for name, value in self._patched.items():
            if value is None:
                if hasattr(self.monitoring, name):
                    delattr(self.monitoring, name)
            else:
                setattr(self.monitoring, name, value)
        self._release_all_advisory_locks()

    # -- helpers ---------------------------------------------------------

    def _session(self):
        session = self.Session()
        self.opened.append(session)
        return session

    def _competing_caller(self) -> int:
        """Check out a connection the way the running application does.

        A Session acquires no connection until it executes, so the statement
        here is the point of the helper: without it the pool is never asked for
        anything and no hand-back can be observed.
        """
        session = self._session()
        return self._backend_pid(session)

    def _begin(self, run_key: str):
        run = self.monitoring.begin_apply_run(
            run_key=run_key,
            release_commit=RELEASE_COMMIT,
            audit_identity="TASK_223_TEST",
        )
        self.runs = getattr(self, "runs", [])
        self.runs.append(run)
        return run

    def _discard(self, run) -> None:
        """Close a run's handles without asserting anything about them."""
        for attribute in ("session", "connection"):
            handle = getattr(run, attribute, None)
            if handle is None:
                continue
            try:
                handle.close()
            except Exception:
                pass

    def _backend_pid(self, executor) -> int:
        from sqlalchemy import text

        return executor.execute(text("SELECT pg_backend_pid()")).scalar()

    def _lock_holder_pids(self) -> list[int]:
        """Backends currently holding this service's session-scoped lock."""
        from sqlalchemy import text

        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT pid FROM pg_locks WHERE locktype='advisory' "
                    "AND classid=:classid AND objid=:objid AND granted"
                ),
                {"classid": self.key >> 32, "objid": self.key & 0xFFFFFFFF},
            ).scalars().all()
        return sorted(rows)

    def _release_all_advisory_locks(self) -> None:
        """Terminate any backend still holding the key, so tests stay isolated."""
        from sqlalchemy import text

        holders = self._lock_holder_pids()
        if not holders:
            return
        with self.engine.connect() as connection:
            own = self._backend_pid(connection)
            for pid in holders:
                if pid == own:
                    connection.execute(
                        text("SELECT pg_advisory_unlock_all()")
                    )
                    continue
                connection.execute(
                    text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}
                )
            connection.commit()
        self.engine.dispose()
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        type(self).engine = create_engine(
            DATABASE_URL, future=True, pool_size=5, max_overflow=5
        )
        type(self).Session = sessionmaker(
            bind=type(self).engine, autocommit=False, autoflush=False
        )
        self.engine = type(self).engine
        self.Session = type(self).Session
        setattr(self.monitoring, "SessionLocal", self.Session)
        if getattr(self.monitoring, "engine", None) is not None:
            setattr(self.monitoring, "engine", self.engine)


class CollectionRunOwnsItsBackend(CollectionRunLockBase):
    """The run must keep the backend that holds its lock."""

    def test_lock_is_held_by_the_backend_the_run_still_executes_on(self):
        run = self._begin("task223-ownership")

        # Another caller checks out a connection, exactly as the web
        # application does while a cycle is running. On the original code the
        # run released its connection at commit, so this caller receives the
        # very backend that holds the run's advisory lock.
        intruder_pid = self._competing_caller()

        run_pid = self._backend_pid(run.session)
        holders = self._lock_holder_pids()

        self.assertEqual(
            holders,
            [run_pid],
            "the advisory lock must be held by the backend the run still uses; "
            f"holders={holders} run_backend={run_pid} other_caller={intruder_pid}",
        )
        self.assertNotEqual(
            holders,
            [intruder_pid],
            "an unrelated caller was handed the backend holding the run lock",
        )

    def test_the_lock_survives_the_many_commits_a_cycle_makes(self):
        run = self._begin("task223-heartbeats")
        start_pid = self._backend_pid(run.session)

        # A real cycle commits once per batch through heartbeat(). Each commit
        # is where a pooled session would hand its backend away.
        for index in range(3):
            self.monitoring.heartbeat(run, {"success_count": index})
            self._competing_caller()  # the application competes for the pool

        self.assertEqual(
            self._backend_pid(run.session),
            start_pid,
            "the run changed backend mid-cycle; its advisory lock is on the old one",
        )
        self.assertEqual(
            self._lock_holder_pids(),
            [start_pid],
            "the lock must still be held by the run's backend after repeated commits",
        )

    def test_a_second_run_is_refused_while_the_first_holds_the_lock(self):
        first = self._begin("task223-first")
        # Drain nothing and take no special care: this is precisely the state a
        # second cycle would find. The lock exists to make this fail.
        with self.assertRaises(RuntimeError) as raised:
            self._begin("task223-second")
        self.assertIn("advisory lock contention", str(raised.exception))
        self.assertIsNotNone(first.run_id)


class CollectionRunReleasesItsLock(CollectionRunLockBase):
    """Finishing a run must release the lock it took."""

    def test_finish_releases_the_advisory_lock(self):
        run = self._begin("task223-release")
        # Force the pool hand-back: another caller takes the connection the run
        # released at commit, so an unlock aimed at "the session" misses.
        self._competing_caller()

        self.monitoring.finish_apply_run(
            run,
            exit_code=0,
            provider_status="healthy",
            counters={"success_count": 1},
            failure_category=None,
        )

        self.assertEqual(
            self._lock_holder_pids(),
            [],
            "the advisory lock leaked: no backend should still hold it after finish",
        )

    def test_finish_records_a_terminal_status(self):
        from sqlalchemy import text

        run = self._begin("task223-terminal")
        self._competing_caller()

        self.monitoring.finish_apply_run(
            run,
            exit_code=0,
            provider_status="healthy",
            counters={"success_count": 1},
            failure_category=None,
        )

        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT status, finished_at FROM satellite_collection_runs "
                    "WHERE run_key=:key"
                ),
                {"key": "task223-terminal"},
            ).mappings().one()
        self.assertEqual(row["status"], "succeeded")
        self.assertIsNotNone(row["finished_at"])


if __name__ == "__main__":
    unittest.main()
