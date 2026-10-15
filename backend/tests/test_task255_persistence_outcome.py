"""TASK_255: a failed satellite_index_records write is never a skip (offline tests).

The writer's typed outcome (INSERTED / ALREADY_EXISTS, WRITE_FAILED as
SatelliteIndexWriteError) and its accounting in ``run_real``: counters, the
field status, the child JSON summary and the process exit code. No database and
no provider: sessions are mocks and the provider is the injected fixture
service. The same contracts run against PostgreSQL in
``test_task255_persistence_postgres.py``.
"""

from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

BACKEND = Path(__file__).resolve().parents[1]
for path in (BACKEND, BACKEND / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import psycopg2  # noqa: E402
from sqlalchemy.exc import IntegrityError, OperationalError  # noqa: E402

from scripts import collect_satellite_indices as collector  # noqa: E402
from scripts.collect_satellite_indices import (  # noqa: E402
    PersistenceOutcome,
    SatelliteIndexWriteError,
)
from task255_support import INDICES, FixtureMultiIndexService  # noqa: E402

DAY = date(2026, 10, 8)
SECRET_MARKERS = ("INSERT INTO", "VALUES", "postgresql://", "hunter2", "bind-value-0.4242", "[SQL", "parameters")


def driver_error(cls=OperationalError, pgcode=None, message="password=hunter2 host=db.internal"):
    """A SQLAlchemy-wrapped DBAPI error whose text must never escape."""
    orig = psycopg2.OperationalError(message)
    if pgcode is not None:
        orig = MagicMock(pgcode=pgcode)
    return cls("INSERT INTO satellite_index_records VALUES (...)", {"mv": "bind-value-0.4242"}, orig)


def write(**overrides):
    values = {"field_id": 7, "captured_date": DAY, "index_code": "ndmi", "mean_value": 0.22,
              "satellite": "Sentinel-2", "enterprise_id": 3}
    values.update(overrides)
    return collector._insert_satellite_index_record(**values)


class OutcomeContractTests(unittest.TestCase):

    def test_an_outcome_has_no_truth_value(self):
        for outcome in PersistenceOutcome:
            with self.subTest(outcome=outcome), self.assertRaises(TypeError):
                bool(outcome)

    def test_write_failed_is_carried_only_by_the_typed_error(self):
        error = SatelliteIndexWriteError(7, DAY, "ndmi", "insert", driver_error(pgcode="25006"))
        self.assertIs(error.outcome, PersistenceOutcome.WRITE_FAILED)
        self.assertEqual(error.evidence(), {
            "field_id": 7, "index_code": "ndmi", "captured_date": "2026-10-08", "outcome": "write_failed",
            "stage": "insert", "error_type": "OperationalError", "sqlstate": "25006",
            "commit_outcome_unknown": False,
        })

    def test_a_malformed_sqlstate_is_dropped(self):
        error = SatelliteIndexWriteError(7, DAY, "ndmi", "insert", driver_error(pgcode="password=x"))
        self.assertIsNone(error.sqlstate)


class WriterTests(unittest.TestCase):

    def setUp(self):
        self.exists = patch.object(collector, "check_exists_by_key", return_value=False)
        self.check = self.exists.start()
        self.factory = patch.object(collector, "SessionLocal")
        self.session_factory = self.factory.start()
        self.session = self.session_factory.return_value
        self.invalidate = patch.object(collector, "_invalidate_observation_cache", return_value=True)
        self.invalidated = self.invalidate.start()

    def tearDown(self):
        for patcher in (self.invalidate, self.factory, self.exists):
            patcher.stop()

    def assert_sanitized(self, error):
        for text in (str(error), error.summary(), json.dumps(error.evidence())):
            for marker in SECRET_MARKERS:
                self.assertNotIn(marker, text)

    def test_existing_key_is_already_exists_without_a_write_session(self):
        self.check.return_value = True
        self.assertIs(write(), PersistenceOutcome.ALREADY_EXISTS)
        self.session_factory.assert_not_called()
        self.invalidated.assert_not_called()

    def test_committed_insert_is_inserted_and_invalidates_after_commit(self):
        order = []
        self.session.execute.return_value.scalar.return_value = 41
        self.session.commit.side_effect = lambda: order.append("commit")
        self.invalidated.side_effect = lambda enterprise: order.append("invalidate")
        self.assertIs(write(), PersistenceOutcome.INSERTED)
        self.assertEqual(order, ["commit", "invalidate"])
        statement = str(self.session.execute.call_args.args[0])
        self.assertIn("ON CONFLICT (field_id, captured_date, index_code) DO NOTHING RETURNING id", statement)
        self.session.close.assert_called_once()

    def test_execute_failure_is_write_failed_rolled_back_and_sanitized(self):
        self.session.execute.side_effect = driver_error(pgcode="08006")
        with self.assertLogs(collector.logger, "ERROR") as logs, self.assertRaises(SatelliteIndexWriteError) as raised:
            write()
        error = raised.exception
        self.assertEqual((error.stage, error.error_type, error.sqlstate, error.commit_outcome_unknown),
                         ("insert", "OperationalError", "08006", False))
        self.assertIsNone(error.__cause__)
        self.assertTrue(error.__suppress_context__)
        self.session.rollback.assert_called_once()
        self.session.commit.assert_not_called()
        self.session.close.assert_called_once()
        self.invalidated.assert_not_called()
        self.assert_sanitized(error)
        for marker in SECRET_MARKERS:
            self.assertNotIn(marker, "\n".join(logs.output))

    def test_commit_failure_is_write_failed_with_an_unknown_commit_outcome(self):
        self.session.execute.return_value.scalar.return_value = 41
        self.session.commit.side_effect = driver_error()
        with self.assertLogs(collector.logger, "ERROR"), self.assertRaises(SatelliteIndexWriteError) as raised:
            write()
        self.assertEqual((raised.exception.stage, raised.exception.commit_outcome_unknown), ("commit", True))
        self.assertIn("commit_outcome=unknown", raised.exception.summary())
        self.session.rollback.assert_called_once()
        self.invalidated.assert_not_called()
        self.assert_sanitized(raised.exception)

    def test_a_failed_rollback_still_reports_the_original_failure(self):
        self.session.execute.side_effect = driver_error()
        self.session.rollback.side_effect = driver_error()
        with self.assertLogs(collector.logger, "WARNING"), self.assertRaises(SatelliteIndexWriteError) as raised:
            write()
        self.assertEqual(raised.exception.stage, "insert")

    def test_a_failed_existence_check_is_write_failed_not_a_skip(self):
        self.check.side_effect = driver_error()
        with self.assertLogs(collector.logger, "ERROR"), self.assertRaises(SatelliteIndexWriteError) as raised:
            write()
        self.assertEqual(raised.exception.stage, "exists_check")
        self.session_factory.assert_not_called()

    def test_a_raised_unique_violation_is_never_already_exists(self):
        # The exact key is arbitrated by ON CONFLICT; a raised 23505 is another constraint.
        self.session.execute.side_effect = driver_error(IntegrityError, pgcode="23505")
        with self.assertLogs(collector.logger, "ERROR"), self.assertRaises(SatelliteIndexWriteError) as raised:
            write()
        self.assertEqual((raised.exception.error_type, raised.exception.sqlstate), ("IntegrityError", "23505"))
        self.session.rollback.assert_called_once()

    def test_a_key_conflict_is_already_exists_only_when_the_row_is_read_back(self):
        inserted = MagicMock(); inserted.scalar.return_value = None
        found = MagicMock(); found.first.return_value = (1,)
        self.session.execute.side_effect = [inserted, found]
        self.assertIs(write(), PersistenceOutcome.ALREADY_EXISTS)
        self.session.commit.assert_not_called()
        self.session.rollback.assert_called_once()
        self.invalidated.assert_not_called()

    def test_a_key_conflict_without_the_row_fails_closed(self):
        inserted = MagicMock(); inserted.scalar.return_value = None
        missing = MagicMock(); missing.first.return_value = None
        self.session.execute.side_effect = [inserted, missing]
        with self.assertLogs(collector.logger, "ERROR"), self.assertRaises(SatelliteIndexWriteError) as raised:
            write()
        self.assertEqual((raised.exception.stage, raised.exception.error_type), ("conflict_check", None))

    def test_a_cache_outage_after_commit_keeps_the_insert(self):
        self.session.execute.return_value.scalar.return_value = 41
        self.invalidated.side_effect = ConnectionError("redis down")
        with self.assertLogs(collector.logger, "WARNING") as logs:
            self.assertIs(write(), PersistenceOutcome.INSERTED)
        self.assertTrue(any("Redis invalidation failed after committed" in line for line in logs.output))
        self.session.commit.assert_called_once()
        self.session.rollback.assert_not_called()

    def test_a_close_failure_after_commit_keeps_the_insert(self):
        self.session.execute.return_value.scalar.return_value = 41
        self.session.close.side_effect = driver_error()
        with self.assertLogs(collector.logger, "WARNING"):
            self.assertIs(write(), PersistenceOutcome.INSERTED)

    def test_overwrite_deletes_and_inserts_in_one_transaction(self):
        self.session.execute.return_value.scalar.return_value = 41
        self.assertIs(write(replace_existing=True), PersistenceOutcome.INSERTED)
        statements = [str(call.args[0]).split()[0] for call in self.session.execute.call_args_list]
        self.assertEqual(statements, ["DELETE", "INSERT"])
        self.session.commit.assert_called_once()
        self.check.assert_not_called()

    def test_a_failed_replacement_rolls_back_the_delete(self):
        self.session.execute.side_effect = [MagicMock(), driver_error()]
        with self.assertLogs(collector.logger, "ERROR"), self.assertRaises(SatelliteIndexWriteError) as raised:
            write(replace_existing=True)
        self.assertEqual(raised.exception.stage, "insert")
        self.session.commit.assert_not_called()
        self.session.rollback.assert_called_once()

    def test_a_failed_overwrite_delete_is_reported(self):
        self.session.execute.side_effect = driver_error(IntegrityError, pgcode="23503")
        with self.assertLogs(collector.logger, "ERROR"), self.assertRaises(SatelliteIndexWriteError) as raised:
            write(replace_existing=True)
        self.assertEqual((raised.exception.stage, raised.exception.sqlstate), ("replace_delete", "23503"))

    def test_a_replacement_that_lost_to_a_concurrent_writer_fails_closed(self):
        deleted = MagicMock()
        inserted = MagicMock(); inserted.scalar.return_value = None
        found = MagicMock(); found.first.return_value = (1,)
        self.session.execute.side_effect = [deleted, inserted, found]
        with self.assertLogs(collector.logger, "ERROR"), self.assertRaises(SatelliteIndexWriteError) as raised:
            write(replace_existing=True)
        self.assertEqual(raised.exception.stage, "conflict_check")
        self.session.commit.assert_not_called()

    def test_provenance_and_enterprise_guards_precede_any_database_access(self):
        with self.assertRaises(RuntimeError):
            write(satellite="Mock/Dev")
        with self.assertRaises(ValueError):
            write(enterprise_id=None)
        self.check.assert_not_called()
        self.session_factory.assert_not_called()


class RunRealAccountingTests(unittest.TestCase):
    """``run_real`` counts every outcome once and exits with what its JSON states."""

    FIELD = {"id": 7, "name": "T255", "enterprise_id": 3, "area_ha": 30.0, "geometry_wkt": "POLYGON((0 0,1 0,1 1,0 0))"}

    def run_collector(self, outcomes=None, *, service=None, extra=()):
        """outcomes: index code -> PersistenceOutcome or an exception to raise."""
        outcomes = outcomes or {}
        calls = []

        def fake_insert(**kwargs):
            calls.append(kwargs)
            result = outcomes.get(kwargs["index_code"], PersistenceOutcome.INSERTED)
            if isinstance(result, BaseException):
                raise result
            return result

        service = service or FixtureMultiIndexService(days=[DAY])
        with tempfile.TemporaryDirectory(prefix="task255-") as directory:
            log = Path(directory) / "child.json"
            args = collector.parse_args(["--write", "--skip-existing", "--field-id", "7", "--indices",
                                         ",".join(INDICES), "--date-from", (DAY - timedelta(days=3)).isoformat(),
                                         "--date-to", DAY.isoformat(), "--max-fields", "1",
                                         "--output-log", str(log), *extra])
            planned = [{"index_code": code, "action": "insert", "existing_count": 0} for code in INDICES]
            with patch.object(collector, "acquire_lock", return_value=None), \
                    patch.object(collector, "release_lock"), \
                    patch.object(collector, "_get_satellite_service", return_value=service), \
                    patch.object(collector, "_query_fields", return_value=[dict(self.FIELD)]), \
                    patch.object(collector, "plan_actions", return_value=planned), \
                    patch.object(collector, "count_existing_satellite_index", return_value=0), \
                    patch.object(collector, "_insert_satellite_index_record", side_effect=fake_insert), \
                    self.assertRaises(SystemExit) as raised:
                collector.run_real(args)
            summary = json.loads(log.read_text(encoding="utf-8"))
        return raised.exception.code, summary, calls

    def failed(self, code):
        return SatelliteIndexWriteError(7, DAY, code, "insert", driver_error(pgcode="25006"))

    def test_mixed_outcomes_in_one_field_fail_the_field_and_the_invocation(self):
        code, summary, calls = self.run_collector({
            "savi": PersistenceOutcome.INSERTED, "evi": PersistenceOutcome.ALREADY_EXISTS,
            "ndmi": self.failed("ndmi"), "ndre": PersistenceOutcome.INSERTED})
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 4)
        self.assertEqual({key: summary[key] for key in (
            "candidates_generated", "db_inserted", "db_skipped", "db_skipped_existing", "db_write_failed",
            "errors", "success_count", "failure_count", "exit_code", "db_writes_performed")}, {
            "candidates_generated": 4, "db_inserted": 2, "db_skipped": 1, "db_skipped_existing": 1,
            "db_write_failed": 1, "errors": 1, "success_count": 0, "failure_count": 1, "exit_code": 1,
            "db_writes_performed": 2})
        self.assertEqual(summary["candidates_generated"],
                         summary["db_inserted"] + summary["db_skipped"] + summary["db_write_failed"])
        field = summary["per_field_results"][0]
        self.assertEqual((field["field_id"], field["status"], field["db_write_failed"]), (7, "write_failed", 1))
        self.assertEqual(field["write_failures"], [self.failed("ndmi").evidence()])
        self.assertEqual(field["errors"], [self.failed("ndmi").summary()])
        for marker in SECRET_MARKERS:
            self.assertNotIn(marker, json.dumps(summary))

    def test_every_failed_key_is_counted_and_the_field_once(self):
        code, summary, _ = self.run_collector({"evi": self.failed("evi"), "ndmi": self.failed("ndmi")})
        self.assertEqual(code, 1)
        self.assertEqual((summary["errors"], summary["db_write_failed"], summary["failure_count"],
                          summary["success_count"], summary["db_inserted"]), (2, 2, 1, 0, 2))
        self.assertEqual([item["index_code"] for item in summary["per_field_results"][0]["write_failures"]],
                         ["evi", "ndmi"])

    def test_all_inserted_is_a_clean_success(self):
        code, summary, _ = self.run_collector()
        self.assertEqual((code, summary["exit_code"], summary["errors"], summary["db_write_failed"]), (0, 0, 0, 0))
        self.assertEqual((summary["db_inserted"], summary["success_count"], summary["failure_count"]), (4, 1, 0))
        self.assertEqual(summary["per_field_results"][0]["status"], "ok")

    def test_all_existing_is_a_clean_skip(self):
        code, summary, _ = self.run_collector({code: PersistenceOutcome.ALREADY_EXISTS for code in INDICES})
        self.assertEqual((code, summary["db_skipped"], summary["db_inserted"], summary["errors"]), (0, 4, 0, 0))

    def test_a_quality_blocked_index_is_not_an_error(self):
        service = FixtureMultiIndexService(days=[DAY], overrides={"evi": {"mean_value": None}})
        code, summary, calls = self.run_collector(service=service)
        self.assertEqual((code, summary["quality_blocked"], summary["db_inserted"], summary["errors"]), (0, 1, 3, 0))
        self.assertNotIn("evi", [call["index_code"] for call in calls])

    def test_no_scene_is_a_clean_empty_run(self):
        code, summary, calls = self.run_collector(service=FixtureMultiIndexService(days=[]))
        self.assertEqual((code, summary["candidates_generated"], summary["errors"], calls), (0, 0, 0, []))
        self.assertEqual(summary["per_field_results"][0]["status"], "ok")

    def test_a_provider_failure_exits_and_reports_nonzero(self):
        service = FixtureMultiIndexService(days=[DAY], error=TimeoutError("provider timeout"))
        code, summary, calls = self.run_collector(service=service)
        self.assertEqual((code, summary["exit_code"], summary["errors"], summary["failure_count"]), (1, 1, 1, 1))
        self.assertEqual(summary["per_field_results"][0]["status"], "collection_error")
        self.assertEqual(calls, [])

    def test_an_unexpected_writer_exception_is_not_swallowed(self):
        with self.assertRaises(ValueError):
            self.run_collector({"savi": ValueError("enterprise_id is required for cache invalidation")})

    def test_overwrite_is_passed_to_the_atomic_writer(self):
        code, summary, calls = self.run_collector(extra=("--overwrite",))
        self.assertEqual(code, 0)
        self.assertTrue(all(call["replace_existing"] is True for call in calls))
        self.assertTrue(summary["overwrite"])

    def test_apply_mode_never_writes(self):
        service = FixtureMultiIndexService(days=[DAY])
        with tempfile.TemporaryDirectory(prefix="task255-") as directory:
            log = Path(directory) / "child.json"
            args = collector.parse_args(["--apply", "--field-id", "7", "--indices", ",".join(INDICES),
                                         "--date-from", DAY.isoformat(), "--date-to", DAY.isoformat(),
                                         "--max-fields", "1", "--output-log", str(log)])
            planned = [{"index_code": code, "action": "insert", "existing_count": 0} for code in INDICES]
            with patch.object(collector, "acquire_lock", return_value=None), \
                    patch.object(collector, "_get_satellite_service", return_value=service), \
                    patch.object(collector, "_query_fields", return_value=[dict(self.FIELD)]), \
                    patch.object(collector, "plan_actions", return_value=planned), \
                    patch.object(collector, "count_existing_satellite_index", return_value=0), \
                    patch.object(collector, "_insert_satellite_index_record", side_effect=AssertionError), \
                    self.assertRaises(SystemExit) as raised:
                collector.run_real(args)
            summary = json.loads(log.read_text(encoding="utf-8"))
        self.assertEqual((raised.exception.code, summary["exit_code"], summary["would_insert"],
                          summary["db_inserted"], summary["db_write_failed"]), (0, 0, 4, 0, 0))

    def test_dry_run_never_writes(self):
        with tempfile.TemporaryDirectory(prefix="task255-") as directory:
            log = Path(directory) / "dry.json"
            args = collector.parse_args(["--dry-run", "--field-id", "7", "--indices", ",".join(INDICES),
                                         "--date-from", DAY.isoformat(), "--date-to", DAY.isoformat(),
                                         "--max-fields", "1", "--output-log", str(log)])
            with patch.object(collector, "acquire_lock", return_value=None), \
                    patch.object(collector, "_query_fields", return_value=[dict(self.FIELD)]), \
                    patch.object(collector, "count_existing_satellite_index", return_value=0), \
                    patch.object(collector, "_query_existing_satellite_index", return_value=[]), \
                    patch.object(collector, "_get_satellite_service", side_effect=AssertionError), \
                    patch.object(collector, "_insert_satellite_index_record", side_effect=AssertionError), \
                    self.assertRaises(SystemExit) as raised:
                collector.run_dry_run(args)
            summary = json.loads(log.read_text(encoding="utf-8"))
        self.assertEqual((raised.exception.code, summary["exit_code"], summary["db_writes"],
                          summary["sentinel_hub_calls"]), (0, 0, 0, 0))


class StaticContractTests(unittest.TestCase):

    def function_source(self, name):
        source = (BACKEND / "scripts" / "collect_satellite_indices.py").read_text(encoding="utf-8-sig")
        node = next(item for item in ast.walk(ast.parse(source))
                    if isinstance(item, ast.FunctionDef) and item.name == name)
        return ast.get_source_segment(source, node)

    def test_the_collection_loop_has_no_separate_delete_transaction(self):
        body = self.function_source("run_real")
        self.assertNotIn("DELETE FROM satellite_index_records", body)
        self.assertNotIn("db.commit()", body)
        self.assertNotIn("check_exists_by_key", body)

    def test_the_writer_never_logs_raw_driver_exceptions(self):
        body = self.function_source("_insert_satellite_index_record")
        self.assertNotIn("logger.exception", body)
        self.assertIn("require_real_provenance(satellite)", body)
        self.assertLess(body.index("db.commit()"), body.index("_invalidate_observation_cache(enterprise_id)"))


if __name__ == "__main__":
    unittest.main()
