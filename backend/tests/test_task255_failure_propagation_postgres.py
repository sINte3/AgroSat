"""TASK_255: a failed index write propagates through every orchestration layer.

    DB write fails -> child exit != 0 with error evidence -> the multi-index runner
    marks the field failed and keeps it for retry -> the batch is non-success ->
    the canonical cycle is not SUCCESS -> the next healthy cycle persists the
    missing key exactly once.

Everything is the production code path except the provider and the database
target. Each collector child is a real ``collect_satellite_indices.py`` process
(``task255_child_launcher.py``: isolated database, no network, injected fixture
service, test-only mutex) with real exit codes and real JSON summaries; the
multi-index runner and the canonical collector are the real modules, called with
their real arguments; the canonical cycle collects every planned batch.
PostgreSQL itself rejects the faulted INSERT (a READ ONLY transaction, 25006).

Skipped unless ``AGROSAT_TEST_DATABASE_URL`` names an isolated ``agrosat_h0a*``
database migrated to head.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from task225_support import RELEASE_COMMIT, Task225Base
from task255_child_launcher import forced_mutex_lock

LAUNCHER = Path(__file__).resolve().parent / "task255_child_launcher.py"
INDICES = "savi,evi,ndmi,ndre"
CYCLE_TEST_MUTEX = "Global\\AgroSat_TASK255_TestCycle_v1"


class FakeDate(date):
    """``date`` whose ``today()`` the test controls (patched into the runner and canonical modules)."""

    current: date | None = None

    @classmethod
    def today(cls):
        return cls.current


class FailurePropagationBase(Task225Base):

    def setUp(self):
        super().setUp()
        import database
        from scripts import collect_satellite as canonical, run_multi_index_collection_cycle as runner
        from services import collector_locking

        self.canonical, self.runner = canonical, runner
        self.directory = tempfile.TemporaryDirectory(prefix="task255-e2e-")
        self.root = Path(self.directory.name)
        (self.root / "child_locks").mkdir()
        # Two active fields keep the real child processes few; the others stay seeded.
        self.sql("UPDATE fields SET is_active = false WHERE id NOT IN (:a, :b)", {"a": self.field_a, "b": self.field_b})
        self.day = self.today - timedelta(days=2)
        self.fixture_path = self.root / "fixture.json"
        self.events_path = self.root / "fault_events.jsonl"
        self.identity_path = self.root / "child_identity.jsonl"
        self.set_fixture([self.day])
        self.patchers = [
            patch.object(database, "SessionLocal", self.Session),
            patch.object(runner, "acquire_lock", forced_mutex_lock(collector_locking.acquire_lock, CYCLE_TEST_MUTEX)),
            patch.object(runner, "date", FakeDate),
            patch.object(canonical, "date", FakeDate),
            patch.dict(os.environ, {
                "AGROSAT_LOCK_DIR": str(self.root / "child_locks"),
                "AGROSAT_TASK255_FIXTURE": str(self.fixture_path),
                "AGROSAT_TASK255_FAULTS": "[]",
                "AGROSAT_TASK255_FAULT_EVENTS": str(self.events_path),
                "AGROSAT_TASK255_IDENTITY_LOG": str(self.identity_path),
                "AGROSAT_RELEASE_COMMIT": RELEASE_COMMIT,
                "AGROSAT_COLLECTOR_AUDIT_IDENTITY": "TASK_255_TEST",
            }),
        ]
        for patcher in self.patchers:
            patcher.start()
        FakeDate.current = self.today
        self.child_commands: list[list[str]] = []

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.directory.cleanup()
        super().tearDown()

    # ── the injected provider and database faults ───────────────────────────

    def set_fixture(self, days):
        self.fixture_path.write_text(json.dumps({"days": [day.isoformat() for day in days]}), encoding="utf-8")

    def set_faults(self, faults):
        os.environ["AGROSAT_TASK255_FAULTS"] = json.dumps(faults)

    def fault_events(self):
        if not self.events_path.exists():
            return []
        return [json.loads(line) for line in self.events_path.read_text(encoding="utf-8").splitlines() if line]

    # ── the real layers ─────────────────────────────────────────────────────

    def launch_child(self, command, timeout):
        """The runner's own subprocess executor, on the real child through the launcher."""
        self.assertTrue(command[1].endswith("collect_satellite_indices.py"))
        self.child_commands.append(command)
        return self.runner.execute_child([sys.executable, str(LAUNCHER), *command[1:]], timeout)

    def run_runner(self, argv):
        return self.runner.run(self.runner.parse_args(argv), child_runner=self.launch_child)

    def canonical_child(self, command, timeout):
        """The canonical collector's batch: the real runner with the command it built."""
        self.assertTrue(command[1].endswith("run_multi_index_collection_cycle.py"))
        code = self.run_runner(command[2:])
        return {"exit_code": code, "timed_out": False, "stdout": "", "stderr": ""}

    # ── reading back ────────────────────────────────────────────────────────

    def stored(self, field):
        return sorted(row["index_code"] for row in self.sql(
            "SELECT index_code FROM satellite_index_records WHERE field_id = :f", {"f": field}))

    def assert_no_duplicates(self):
        self.assertEqual(self.scalar(
            "SELECT count(*) FROM (SELECT field_id, captured_date, index_code FROM satellite_index_records "
            "GROUP BY 1,2,3 HAVING count(*) > 1) d"), 0)

    def assert_children_used_the_isolated_database(self):
        lines = self.identity_path.read_text(encoding="utf-8").splitlines()
        self.assertGreater(len(lines), 0)
        for line in lines:
            identity = json.loads(line)
            self.assertTrue(identity["current_database"].startswith("agrosat_h0a"))
            self.assertNotEqual(identity["current_database"], "agrosat")

    @staticmethod
    def one(directory: Path, pattern: str) -> Path:
        found = list(directory.glob(pattern))
        assert len(found) == 1, found
        return found[0]


class BatchPropagationTests(FailurePropagationBase):

    def runner_argv(self, output):
        return ["--write", "--all-active-fields", "--batch-size", "2", "--indices", INDICES,
                "--date-from", (self.today - timedelta(days=14)).isoformat(), "--date-to", self.today.isoformat(),
                "--max-attempts", "2", "--retry-base-seconds", "0",
                "--output-dir", str(self.root / output), "--state-file", str(self.root / "state" / "multi_state.json"),
                "--lock-file", str(self.root / "runner.lock")]

    def test_a_failed_write_fails_the_field_keeps_it_for_retry_and_fails_the_batch(self):
        a, b = self.field_a, self.field_b
        self.set_faults([{"kind": "insert_read_only", "field_id": a, "index_code": "ndmi"}])
        code = self.run_runner(self.runner_argv("out1"))

        self.assertEqual(code, 1)
        run_dir = self.one(self.root / "out1", "cycle_*")
        summary = json.loads((run_dir / "cycle_summary.json").read_text(encoding="utf-8"))
        self.assertEqual({key: summary[key] for key in (
            "exit_code", "selected_field_ids", "successful_field_ids", "failed_field_ids", "unattempted_field_ids",
            "success_count", "failure_count", "unattempted_count", "retry_queue_after", "attempt_counts",
            "inserted_count", "skipped_existing_count", "provider_failure_category", "state_advanced")}, {
            "exit_code": 1, "selected_field_ids": [a, b], "successful_field_ids": [b], "failed_field_ids": [a],
            "unattempted_field_ids": [], "success_count": 1, "failure_count": 1, "unattempted_count": 0,
            "retry_queue_after": [a], "attempt_counts": 3, "inserted_count": 7, "skipped_existing_count": 3,
            "provider_failure_category": None, "state_advanced": True})
        state = json.loads((self.root / "state" / "multi_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["retry_field_ids"], [a])

        records = [json.loads(line) for line in (run_dir / "field_results.jsonl").read_text(encoding="utf-8").splitlines()]
        failed = [record for record in records if record["field_id"] == a]
        self.assertEqual([(r["attempt"], r["exit_code"], r["classification"]) for r in failed],
                         [(1, 1, "RETRYABLE_FAILURE"), (2, 1, "RETRYABLE_FAILURE")])
        for record, (inserted, skipped) in zip(failed, ((3, 0), (0, 3))):
            child = record["child_summary"]
            self.assertEqual((child["exit_code"], child["errors"], child["failure_count"], child["db_write_failed"],
                              child["db_inserted"], child["db_skipped"]), (1, 1, 1, 1, inserted, skipped))
            field = child["per_field_results"][0]
            self.assertEqual((field["status"], field["write_failures"][0]["index_code"],
                              field["write_failures"][0]["sqlstate"]), ("write_failed", "ndmi", "25006"))
        succeeded = [record for record in records if record["field_id"] == b]
        self.assertEqual([(r["exit_code"], r["classification"], r["child_summary"]["db_inserted"]) for r in succeeded],
                         [(0, "SUCCESS", 4)])
        self.assertEqual([(e["field_id"], e["index_code"]) for e in self.fault_events()], [(a, "ndmi"), (a, "ndmi")])
        self.assertEqual(self.stored(a), ["evi", "ndre", "savi"])
        self.assertEqual(self.stored(b), ["evi", "ndmi", "ndre", "savi"])
        self.assert_children_used_the_isolated_database()

        # The retry: the database accepts the write again; the field is retried first.
        self.set_faults([])
        self.assertEqual(self.run_runner(self.runner_argv("out2")), 0)
        retry = json.loads((self.one(self.root / "out2", "cycle_*") / "cycle_summary.json").read_text(encoding="utf-8"))
        self.assertEqual((retry["selected_field_ids"][0], retry["batch_source_counts"]["retry"],
                          retry["failed_field_ids"], retry["retry_queue_after"], retry["inserted_count"]),
                         (a, 1, [], [], 1))
        self.assertEqual(self.stored(a), ["evi", "ndmi", "ndre", "savi"])
        self.assert_no_duplicates()


class CanonicalCyclePropagationTests(FailurePropagationBase):
    """TASK_262: the canonical cycle of the candidate base, end to end, with no collection preflight."""

    def setUp(self):
        super().setUp()
        self.cycles = 0

    def cycle(self):
        """One canonical apply cycle over every active field, with the flags the Scheduled Task uses."""
        from config import settings

        self.cycles += 1
        run_id = f"t262run{self.cycles:03d}"
        children_before = len(self.child_commands)
        flags = ["--apply", "--all-active-fields", "--batch-size", "5", "--indices", INDICES,
                 "--max-attempts", "2", "--retry-base-seconds", "0",
                 "--output-dir", str(self.root / "output"), "--state-dir", str(self.root / "state"),
                 "--lock-dir", str(self.root / "locks")]
        with patch.object(settings, "observation_detection_enabled", False):
            code, summary = self.canonical.run(
                self.canonical.parse_args(flags), child_runner=self.canonical_child,
                lock_acquire=lambda *a, **k: "lock", lock_release=lambda lock: None,
                run_id_factory=lambda: run_id)
        run = self.sql("SELECT status, provider_status, failure_category FROM satellite_collection_runs "
                       "ORDER BY started_at DESC, id DESC LIMIT 1")[0]
        latest = json.loads((self.root / "output" / "collector_latest_status.json").read_text(encoding="utf-8"))
        batch = json.loads(self.one(self.root / "output", f"run_{run_id}/multi_batch_0001/cycle_*/cycle_summary.json")
                           .read_text(encoding="utf-8"))
        return code, summary, run, latest, batch, len(self.child_commands) - children_before

    def images(self):
        """Every stored key with its row identity: a row never rewritten keeps its id and xmin."""
        return {(row["field_id"], row["captured_date"].isoformat(), row["index_code"]): (row["id"], row["xmin"])
                for row in self.sql("SELECT id, xmin::text AS xmin, field_id, captured_date, index_code "
                                    "FROM satellite_index_records")}

    def key_count(self, field, day, code):
        return self.scalar("SELECT count(*) FROM satellite_index_records "
                           "WHERE field_id = :f AND captured_date = :d AND index_code = :c",
                           {"f": field, "d": day, "c": code})

    def test_a_failed_write_blocks_canonical_success_and_the_healthy_retry_persists_it_once(self):
        a, b = self.field_a, self.field_b

        # 1. PostgreSQL rejects field A's NDMI write on every attempt.
        self.set_faults([{"kind": "insert_read_only", "field_id": a, "index_code": "ndmi"}])
        code, summary, run, latest, batch, children = self.cycle()
        self.assertEqual(code, 1, summary["diagnostics"])
        self.assertEqual((summary["exit_code"], summary["run_status"]), (1, "PARTIAL"))
        stage = summary["stages"]["multi"]
        self.assertEqual((stage["completed"], stage["failed"], stage["unfinished_field_ids"],
                          stage["unverified_batch_count"]), (1, 1, [a], 0))
        # "partial" is stored as the database vocabulary's "operational".
        self.assertEqual((run["status"], run["provider_status"], run["failure_category"]),
                         ("degraded", "degraded", "operational"))
        self.assertEqual((latest["status"], latest["exit_code"]), ("failed", 1))
        self.assertFalse((self.root / "output" / "collector_last_success.json").exists())
        self.assertEqual(children, 3)
        self.assertEqual((batch["exit_code"], batch["failed_field_ids"], batch["successful_field_ids"],
                          batch["retry_queue_after"], batch["inserted_count"], batch["skipped_existing_count"]),
                         (1, [a], [b], [a], 7, 3))
        records = [json.loads(line) for line in self.one(self.root / "output", "run_t262run001/multi_batch_0001/cycle_*")
                   .joinpath("field_results.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(r["attempt"], r["exit_code"], r["child_summary"]["exit_code"],
                           r["child_summary"]["db_write_failed"], r["child_summary"]["db_skipped"])
                          for r in records if r["field_id"] == a], [(1, 1, 1, 1, 0), (2, 1, 1, 1, 3)])
        self.assertEqual([(e["field_id"], e["index_code"]) for e in self.fault_events()], [(a, "ndmi"), (a, "ndmi")])
        # No falsely fresh date: the failed key has no row, so A's NDMI has no date at all.
        self.assertEqual(self.key_count(a, self.day, "ndmi"), 0)
        self.assertIsNone(self.scalar("SELECT max(captured_date) FROM satellite_index_records "
                                      "WHERE field_id = :f AND index_code = 'ndmi'", {"f": a}))
        self.assertEqual(self.stored(a), ["evi", "ndre", "savi"])
        self.assertEqual(self.stored(b), ["evi", "ndmi", "ndre", "savi"])
        before = self.images()
        self.assertEqual(len(before), 7)

        # 2. Same acquisitions, database healthy again: the retry persists exactly the
        #    missing key. Positive control: every truly existing key stays a skip.
        self.set_faults([])
        code, summary, run, latest, batch, children = self.cycle()
        self.assertEqual((code, summary["run_status"]), (0, "SUCCESS"), summary["diagnostics"])
        self.assertEqual((run["status"], run["provider_status"], run["failure_category"]),
                         ("succeeded", "healthy", None))
        self.assertEqual((latest["status"], latest["exit_code"]), ("succeeded", 0))
        self.assertEqual(children, 2)
        self.assertEqual((batch["exit_code"], batch["selected_field_ids"][0], batch["failed_field_ids"],
                          batch["retry_queue_after"], batch["inserted_count"], batch["skipped_existing_count"]),
                         (0, a, [], [], 1, 7))
        self.assertEqual(self.key_count(a, self.day, "ndmi"), 1)
        after = self.images()
        self.assertEqual(set(after) - set(before), {(a, self.day.isoformat(), "ndmi")})
        self.assertEqual({key: after[key] for key in before}, before)
        self.assert_no_duplicates()

        # 3. Positive control: a new accepted acquisition commits for every field and index,
        #    and the stored keys are still never rewritten.
        new_day = self.day + timedelta(days=1)
        self.set_fixture([self.day, new_day])
        code, summary, run, latest, batch, children = self.cycle()
        self.assertEqual((code, summary["run_status"], children), (0, "SUCCESS", 2), summary["diagnostics"])
        self.assertEqual((batch["inserted_count"], batch["skipped_existing_count"], batch["failed_field_ids"]),
                         (8, 8, []))
        final = self.images()
        self.assertEqual(len(final), 16)
        self.assertEqual({key: final[key] for key in after}, after)
        for field in (a, b):
            for code_name in INDICES.split(","):
                self.assertEqual(self.key_count(field, new_day, code_name), 1)
        self.assertEqual(len(self.fault_events()), 2)    # only the two faulted attempts of cycle 1
        self.assert_no_duplicates()
        self.assert_children_used_the_isolated_database()
