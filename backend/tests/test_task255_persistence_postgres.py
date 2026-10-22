"""TASK_255: satellite_index_records persistence outcomes against real PostgreSQL.

Isolated database migrated to head (the shared ``Task225Base`` guard refuses any
database whose name does not start with ``agrosat_h0a``; this suite also checks
``current_database()`` before its first write). Failures are PostgreSQL's own:
a READ ONLY transaction rejecting the INSERT (25006), a terminated backend before
COMMIT, a foreign key (23503), NOT NULL (23502) and primary key (23505)
violation. Only the backend a test session owns is ever terminated.

The table has no CHECK constraint (see the metadata test), so a CHECK violation
cannot occur on this write path.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from task225_support import Task225Base
from task255_support import INDICES, FixtureMultiIndexService, faulty_sessionmaker

UNIQUE_KEY = "UNIQUE (field_id, captured_date, index_code)"


class PersistencePostgresBase(Task225Base):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from sqlalchemy import text

        with cls.engine.connect() as connection:
            name = connection.execute(text("SELECT current_database()")).scalar_one()
        if name == "agrosat" or not name.startswith("agrosat_h0a"):
            raise RuntimeError("TASK_255 refuses a non-isolated database")

    def setUp(self):
        super().setUp()
        from scripts import collect_satellite_indices as collector
        from services import collector_idempotency

        self.collector = collector
        self.day = self.today - timedelta(days=2)
        self.invalidations = []
        self.patchers = [
            patch.object(collector, "SessionLocal", self.Session),
            patch.object(collector_idempotency, "SessionLocal", self.Session),
            patch.object(collector, "_invalidate_observation_cache", self.invalidations.append),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        super().tearDown()

    def write(self, code="ndmi", *, field=None, day=None, mean=0.22, **overrides):
        values = {"field_id": field or self.field_a, "captured_date": day or self.day, "index_code": code,
                  "mean_value": mean, "satellite": "Sentinel-2", "min_value": mean - 0.2, "max_value": mean + 0.2,
                  "std_value": 0.05, "valid_pixels_pct": 96.0, "enterprise_id": self.enterprise_a}
        values.update(overrides)
        return self.collector._insert_satellite_index_record(**values)

    def seed_row(self, code="ndmi", mean=0.11, field=None, day=None):
        return self.scalar(
            "INSERT INTO satellite_index_records (field_id, captured_date, index_code, mean_value, satellite) "
            "VALUES (:f, :d, :c, :m, 'Sentinel-2') RETURNING id",
            {"f": field or self.field_a, "d": day or self.day, "c": code, "m": mean})

    def rows(self, code="ndmi", field=None, day=None):
        return self.sql(
            "SELECT id, xmin::text AS xmin, mean_value, satellite, created_at FROM satellite_index_records "
            "WHERE field_id = :f AND captured_date = :d AND index_code = :c",
            {"f": field or self.field_a, "d": day or self.day, "c": code})

    def faulty(self, kind, code="ndmi", field=None):
        return patch.object(self.collector, "SessionLocal", faulty_sessionmaker(
            self.engine, [{"kind": kind, "field_id": field or self.field_a, "index_code": code}]))

    def sequence(self):
        return self.sql("SELECT last_value, is_called FROM satellite_index_records_id_seq")[0]

    def idle_in_transaction(self):
        return self.scalar("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                           "AND state LIKE 'idle in transaction%' AND pid <> pg_backend_pid()")


class WriterOutcomePostgresTests(PersistencePostgresBase):

    def test_metadata_enforces_the_exact_key_uniqueness_without_check_constraints(self):
        constraints = self.sql(
            "SELECT contype, pg_get_constraintdef(oid) AS definition, condeferrable FROM pg_constraint "
            "WHERE conrelid = 'public.satellite_index_records'::regclass")
        unique = [row for row in constraints if row["contype"] == "u" and row["definition"] == UNIQUE_KEY]
        self.assertEqual(len(unique), 1)
        self.assertFalse(unique[0]["condeferrable"])
        self.assertEqual([row for row in constraints if row["contype"] == "c"], [])
        # Enforced by the database itself, whatever the writer does.
        self.seed_row()
        from sqlalchemy.exc import IntegrityError
        with self.assertRaises(IntegrityError) as raised:
            self.seed_row(mean=0.5)
        self.assertEqual(raised.exception.orig.pgcode, "23505")

    def test_a_confirmed_duplicate_is_already_exists_without_any_mutation(self):
        self.seed_row(mean=0.11)
        before, sequence = self.rows(), self.sequence()
        self.assertIs(self.write(mean=0.99), self.collector.PersistenceOutcome.ALREADY_EXISTS)
        self.assertEqual(self.rows(), before)            # same id, xmin, value and timestamp
        self.assertEqual(self.sequence(), sequence)      # no INSERT was even attempted
        self.assertEqual(self.invalidations, [])

    def test_a_committed_insert_is_inserted_and_the_same_key_then_skips(self):
        self.assertIs(self.write(), self.collector.PersistenceOutcome.INSERTED)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.invalidations, [self.enterprise_a])
        self.assertIs(self.write(mean=0.5), self.collector.PersistenceOutcome.ALREADY_EXISTS)
        rows = self.rows()
        self.assertEqual((len(rows), rows[0]["mean_value"]), (1, 0.22))
        self.assertEqual(self.invalidations, [self.enterprise_a])

    def test_an_execute_failure_rolls_back_and_raises_write_failed(self):
        with self.faulty("insert_read_only"), self.assertRaises(self.collector.SatelliteIndexWriteError) as raised:
            self.write()
        error = raised.exception
        self.assertEqual((error.stage, error.error_type, error.sqlstate, error.commit_outcome_unknown),
                         ("insert", "InternalError", "25006", False))
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.idle_in_transaction(), 0)
        self.assertEqual(self.invalidations, [])
        # The rolled-back session left nothing behind: the next write succeeds.
        self.assertIs(self.write(), self.collector.PersistenceOutcome.INSERTED)

    def test_a_commit_failure_after_backend_termination_is_write_failed_and_nothing_commits(self):
        with self.faulty("commit_terminate"), self.assertRaises(self.collector.SatelliteIndexWriteError) as raised:
            self.write()
        error = raised.exception
        self.assertEqual((error.stage, error.error_type, error.commit_outcome_unknown),
                         ("commit", "OperationalError", True))
        self.assertEqual(self.rows(), [])                # the server never committed it
        self.assertEqual(self.invalidations, [])
        self.assertIs(self.write(), self.collector.PersistenceOutcome.INSERTED)
        self.assertEqual(len(self.rows()), 1)

    def test_an_ambiguous_commit_fails_closed_and_the_next_run_reads_the_key(self):
        with self.faulty("commit_lost_reply"), self.assertRaises(self.collector.SatelliteIndexWriteError) as raised:
            self.write()
        self.assertEqual((raised.exception.stage, raised.exception.commit_outcome_unknown), ("commit", True))
        self.assertEqual(len(self.rows()), 1)            # it did commit, but that was never claimed
        self.assertEqual(self.invalidations, [])
        self.assertIs(self.write(), self.collector.PersistenceOutcome.ALREADY_EXISTS)
        self.assertEqual(len(self.rows()), 1)

    def test_unrelated_integrity_violations_are_never_already_exists(self):
        error_type = self.collector.SatelliteIndexWriteError
        with self.assertRaises(error_type) as raised:       # foreign key: no such field
            self.write(field=self.field_a + 100_000)
        self.assertEqual((raised.exception.stage, raised.exception.sqlstate), ("insert", "23503"))
        with self.assertRaises(error_type) as raised:       # NOT NULL captured_date
            self.collector._insert_satellite_index_record(
                field_id=self.field_a, captured_date=None, index_code="ndmi", mean_value=0.2,
                satellite="Sentinel-2", enterprise_id=self.enterprise_a)
        self.assertEqual(raised.exception.sqlstate, "23502")
        # A unique violation that is not the exact key: the primary key, after the id
        # sequence is moved back onto an existing row.
        existing = self.seed_row(code="savi")
        self.sql("SELECT setval('satellite_index_records_id_seq', :v, false)", {"v": existing})
        with self.assertRaises(error_type) as raised:
            self.write("ndmi")
        self.assertEqual((raised.exception.error_type, raised.exception.sqlstate), ("IntegrityError", "23505"))
        self.assertEqual(self.rows("ndmi"), [])
        self.assertEqual(self.idle_in_transaction(), 0)
        self.assertEqual(self.invalidations, [])

    def test_a_key_committed_after_the_precheck_is_a_verified_concurrent_skip(self):
        from services import collector_idempotency

        real_check = collector_idempotency.check_exists_by_key

        def check_then_race(*key):
            found = real_check(*key)
            self.seed_row(mean=0.77)                         # another writer commits the key
            return found

        with patch.object(self.collector, "check_exists_by_key", side_effect=check_then_race):
            self.assertIs(self.write(mean=0.22), self.collector.PersistenceOutcome.ALREADY_EXISTS)
        rows = self.rows()
        self.assertEqual((len(rows), rows[0]["mean_value"]), (1, 0.77))   # the other writer's row, untouched
        self.assertEqual(self.invalidations, [])

    def _race_uncommitted(self, competitor_commits):
        from sqlalchemy import text

        competitor = self.engine.connect()
        transaction = competitor.begin()
        competitor.execute(text(
            "INSERT INTO satellite_index_records (field_id, captured_date, index_code, mean_value, satellite) "
            "VALUES (:f, :d, 'ndmi', 0.66, 'Sentinel-2')"), {"f": self.field_a, "d": self.day})
        outcome = {}

        def writer():
            try:
                outcome["value"] = self.write(mean=0.22)
            except Exception as exc:  # reported through the assertion below
                outcome["error"] = exc

        thread = threading.Thread(target=writer, daemon=True)
        try:
            thread.start()
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                waiting = self.scalar("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                                      "AND wait_event_type = 'Lock'")
                if waiting:
                    break
                time.sleep(0.05)
            self.assertTrue(waiting, "the writer never blocked on the competitor's uncommitted key")
        finally:
            (transaction.commit if competitor_commits else transaction.rollback)()
            competitor.close()
            thread.join(30)
        self.assertFalse(thread.is_alive())
        self.assertNotIn("error", outcome, outcome)
        return outcome["value"]

    def test_a_concurrent_uncommitted_writer_that_commits_wins_and_we_skip(self):
        self.assertIs(self._race_uncommitted(True), self.collector.PersistenceOutcome.ALREADY_EXISTS)
        rows = self.rows()
        self.assertEqual((len(rows), rows[0]["mean_value"]), (1, 0.66))

    def test_a_concurrent_writer_that_rolls_back_leaves_our_insert(self):
        self.assertIs(self._race_uncommitted(False), self.collector.PersistenceOutcome.INSERTED)
        rows = self.rows()
        self.assertEqual((len(rows), rows[0]["mean_value"]), (1, 0.22))

    def test_a_cache_outage_after_commit_keeps_one_committed_insert(self):
        def outage(enterprise):
            raise ConnectionError("cache unavailable")

        with patch.object(self.collector, "_invalidate_observation_cache", side_effect=outage), \
                self.assertLogs(self.collector.logger, "WARNING") as logs:
            self.assertIs(self.write(), self.collector.PersistenceOutcome.INSERTED)
        self.assertTrue(any("Redis invalidation failed after committed" in line for line in logs.output))
        self.assertEqual(len(self.rows()), 1)
        self.assertIs(self.write(), self.collector.PersistenceOutcome.ALREADY_EXISTS)
        self.assertEqual(len(self.rows()), 1)


class OverwritePostgresTests(PersistencePostgresBase):

    def test_a_failed_replacement_insert_keeps_the_original_row(self):
        self.seed_row(mean=0.11)
        before = self.rows()
        with self.faulty("insert_read_only"), self.assertRaises(self.collector.SatelliteIndexWriteError) as raised:
            self.write(mean=0.55, replace_existing=True)
        self.assertEqual((raised.exception.stage, raised.exception.sqlstate), ("insert", "25006"))
        self.assertEqual(self.rows(), before)            # the DELETE was rolled back with it

    def test_a_failed_replacement_commit_keeps_the_original_row(self):
        self.seed_row(mean=0.11)
        before = self.rows()
        with self.faulty("commit_terminate"), self.assertRaises(self.collector.SatelliteIndexWriteError):
            self.write(mean=0.55, replace_existing=True)
        self.assertEqual(self.rows(), before)

    def test_a_failed_delete_is_reported_and_keeps_the_original_row(self):
        self.seed_row(mean=0.11)
        before = self.rows()
        with self.faulty("delete_read_only"), self.assertRaises(self.collector.SatelliteIndexWriteError) as raised:
            self.write(mean=0.55, replace_existing=True)
        self.assertEqual((raised.exception.stage, raised.exception.sqlstate), ("replace_delete", "25006"))
        self.assertEqual(self.rows(), before)

    def test_a_successful_replacement_is_one_atomic_swap(self):
        original = self.seed_row(mean=0.11)
        self.assertIs(self.write(mean=0.55, replace_existing=True), self.collector.PersistenceOutcome.INSERTED)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0]["id"], original)
        self.assertEqual(rows[0]["mean_value"], 0.55)


class MixedFieldPostgresTests(PersistencePostgresBase):
    """One field, four indices, real database: inserted, existing and failed keys reconcile."""

    def test_one_failed_index_fails_the_field_and_the_invocation(self):
        from services import collector_locking
        from task255_child_launcher import forced_mutex_lock

        self.seed_row(code="savi", mean=0.4)
        service = FixtureMultiIndexService(days=[self.day])
        with tempfile.TemporaryDirectory(prefix="task255-") as directory:
            log = Path(directory) / "child.json"
            args = self.collector.parse_args([
                "--write", "--skip-existing", "--field-id", str(self.field_a), "--indices", ",".join(INDICES),
                "--date-from", (self.today - timedelta(days=14)).isoformat(), "--date-to", self.today.isoformat(),
                "--max-fields", "1", "--output-log", str(log), "--lock-file", str(Path(directory) / "child.lock")])
            lock = forced_mutex_lock(collector_locking.acquire_lock)
            with self.faulty("insert_read_only", code="ndmi"), \
                    patch.object(self.collector, "acquire_lock", side_effect=lock), \
                    patch.object(self.collector, "_get_satellite_service", return_value=service), \
                    self.assertRaises(SystemExit) as raised:
                self.collector.run_real(args)
            summary = json.loads(log.read_text(encoding="utf-8"))
        self.assertEqual(raised.exception.code, 1)
        self.assertEqual({key: summary[key] for key in (
            "candidates_generated", "db_inserted", "db_skipped", "db_write_failed", "errors",
            "success_count", "failure_count", "exit_code")}, {
            "candidates_generated": 4, "db_inserted": 2, "db_skipped": 1, "db_write_failed": 1, "errors": 1,
            "success_count": 0, "failure_count": 1, "exit_code": 1})
        field = summary["per_field_results"][0]
        self.assertEqual((field["status"], [item["index_code"] for item in field["write_failures"]]),
                         ("write_failed", ["ndmi"]))
        self.assertEqual(field["write_failures"][0]["sqlstate"], "25006")
        stored = {row["index_code"] for row in self.sql(
            "SELECT index_code FROM satellite_index_records WHERE field_id = :f", {"f": self.field_a})}
        self.assertEqual(stored, {"savi", "evi", "ndre"})
        self.assertEqual(self.invalidations, [self.enterprise_a, self.enterprise_a])
