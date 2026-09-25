"""TASK_229 / Part G: PostgreSQL proof that operational state never strands
or rewrites a collection run.

The canonical collector runs in apply mode against a real database migrated
to head. ``begin_apply_run`` takes the advisory lock and inserts the run, the
freshness, anomaly and verification steps run for real, and
``finish_apply_run`` terminalizes the run. The stand-ins are the provider
child (no network) and ``os.replace`` (``ReplaceFaults``: real reader
collisions on Windows).

On 7d1a975 a reader at the final status publication left a ``succeeded`` run
behind a Scheduled Task exit code 4, and a reader at the final heartbeat
recorded the run itself as ``failed``.

The suite is skipped unless ``AGROSAT_TEST_DATABASE_URL`` is set. It refuses
any database whose name does not start with ``agrosat_h0a``; the production
database is named ``agrosat`` and is rejected before any statement runs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid

from task229_support import DISK_FULL, READER, ReplaceFaults, Rule


ISOLATED_PREFIX = "agrosat_h0a"
PRODUCTION_DATABASE = "agrosat"
RELEASE_COMMIT = "0" * 40
LATEST = "collector_latest_status.json"
HEARTBEAT = "collector_heartbeat.json"
FINAL = frozenset({2})  # the running snapshot (or first beat) is publication 1


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


def healthy_child(command, timeout):
    provider_output = Path(command[command.index("--output-dir") + 1])
    (provider_output / "cycle_1").mkdir()
    (provider_output / "cycle_1" / "cycle_summary.json").write_text(
        json.dumps({
            "success_count": 1, "failure_count": 0, "inserted_count": 1,
            "skipped_existing_count": 0, "quality_blocked_count": 0, "timeout_count": 0,
        }),
        encoding="utf-8",
    )
    return {"exit_code": 0, "timed_out": False, "stdout": "ok", "stderr": ""}


@unittest.skipIf(DATABASE_URL is None, "AGROSAT_TEST_DATABASE_URL is not set")
class OperationalStateNeverStrandsTheRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        cls.engine = create_engine(DATABASE_URL, future=True)
        cls.Session = sessionmaker(bind=cls.engine, autocommit=False, autoflush=False)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        import database
        from services import autonomous_monitoring

        self.lock_key = autonomous_monitoring.ADVISORY_LOCK_KEY
        # The collector reaches the database through these handles.
        for module, name, value in (
            (autonomous_monitoring, "SessionLocal", self.Session),
            (autonomous_monitoring, "engine", self.engine),
            (database, "SessionLocal", self.Session),
        ):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.identities: list[str] = []
        self.addCleanup(self._delete_own_runs)

    def _delete_own_runs(self):
        from sqlalchemy import text

        with self.engine.begin() as connection:
            for identity in self.identities:
                connection.execute(
                    text("DELETE FROM satellite_collection_runs WHERE audit_identity=:identity"),
                    {"identity": identity},
                )

    def _cycle(self, label: str, rules: dict[str, Rule] | None = None):
        from sqlalchemy import text

        from config import settings
        from scripts import collect_satellite as collector

        identity = f"TASK_229_{label}_{uuid.uuid4().hex[:8]}"
        self.identities.append(identity)
        run_id = uuid.uuid4().hex
        args = collector.parse_args(
            [
                "--apply", "--field-ids", "4", "--indices", "ndvi",
                "--output-dir", str(self.root / label / "output"),
                "--state-dir", str(self.root / label / "state"),
                "--lock-dir", str(self.root / label / "locks"),
            ]
        )
        faults = ReplaceFaults(rules)
        with patch.object(os, "replace", faults), \
                patch.dict(os.environ, {
                    "AGROSAT_RELEASE_COMMIT": RELEASE_COMMIT,
                    "AGROSAT_COLLECTOR_AUDIT_IDENTITY": identity,
                }), \
                patch.object(settings, "observation_detection_enabled", False):
            code, summary = collector.run(
                args,
                child_runner=healthy_child,
                lock_acquire=lambda *args, **kwargs: "lock",
                lock_release=lambda lock: None,
                run_id_factory=lambda: run_id,
            )
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT status, provider_status, failure_category, finished_at "
                    "FROM satellite_collection_runs WHERE audit_identity=:identity"
                ),
                {"identity": identity},
            ).mappings().all()
        self.assertEqual(len(rows), 1, "the cycle must have recorded exactly one run")
        return code, summary, faults, dict(rows[0])

    def _lock_holders(self) -> int:
        from sqlalchemy import text

        with self.engine.connect() as connection:
            return connection.execute(
                text(
                    "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND granted "
                    "AND classid=:classid AND objid=:objid AND database="
                    "(SELECT oid FROM pg_database WHERE datname=current_database())"
                ),
                {"classid": self.lock_key >> 32, "objid": self.lock_key & 0xFFFFFFFF},
            ).scalar_one()

    def assertTerminalSucceeded(self, row):
        self.assertEqual(row["status"], "succeeded")
        self.assertEqual(row["provider_status"], "healthy")
        self.assertIsNone(row["failure_category"])
        self.assertIsNotNone(row["finished_at"])
        self.assertEqual(self._lock_holders(), 0, "the run's advisory lock must be released")

    # -- transient collisions ----------------------------------------------------

    def test_a_reader_at_the_final_status_leaves_a_succeeded_run_and_exit_0(self):
        code, summary, faults, row = self._cycle(
            "final_status_reader", {LATEST: Rule(READER, publications=FINAL)}
        )

        self.assertEqual(code, 0, summary["diagnostics"])  # 7d1a975: 4
        self.assertTerminalSucceeded(row)
        self.assertEqual(
            [outcome for publication, outcome in faults.attempts(LATEST) if publication == 2],
            ["winerror=5", "replaced"],
        )

    def test_a_reader_at_the_final_heartbeat_leaves_a_succeeded_run_and_exit_0(self):
        code, summary, faults, row = self._cycle(
            "final_heartbeat_reader", {HEARTBEAT: Rule(READER, publications=FINAL)}
        )

        self.assertEqual(code, 0, summary["diagnostics"])  # 7d1a975: 4
        self.assertTerminalSucceeded(row)  # 7d1a975: failed / degraded / operational

    # -- permanent failures ------------------------------------------------------

    def test_a_permanent_status_failure_leaves_the_run_terminal_and_succeeded(self):
        code, summary, _, row = self._cycle(
            "final_status_disk_full",
            {LATEST: Rule(DISK_FULL, publications=FINAL, failures=None)},
        )

        self.assertEqual(code, 4)
        self.assertTrue(
            any("latest status persistence failed" in item for item in summary["diagnostics"])
        )
        self.assertTerminalSucceeded(row)

    def test_a_permanent_heartbeat_failure_leaves_the_run_terminal_and_succeeded(self):
        code, summary, _, row = self._cycle(
            "final_heartbeat_disk_full",
            {HEARTBEAT: Rule(DISK_FULL, publications=FINAL, failures=None)},
        )

        self.assertEqual(code, 4)
        self.assertTrue(
            any("heartbeat persistence failed" in item for item in summary["diagnostics"])
        )
        self.assertTerminalSucceeded(row)  # 7d1a975: failed / degraded / operational


if __name__ == "__main__":
    unittest.main()
