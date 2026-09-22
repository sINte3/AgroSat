"""H0-A: PostgreSQL-backed proof of the accepted-observation contract repair.

These tests run the real freshness and closed-loop code against a real
PostgreSQL/PostGIS database migrated to the production Alembic head, using
production-shaped rows: ``satellite = 'Sentinel-2'``, a populated
``valid_pixels_pct`` and a NULL ``cloud_cover_pct``.

The suite is skipped unless ``AGROSAT_TEST_DATABASE_URL`` is set. It is
destructive within its own database, so it refuses any target whose database
name does not start with ``agrosat_h0a``. The production database is named
``agrosat`` and is therefore rejected before any statement runs.

    createdb agrosat_h0a_contract
    psql -d agrosat_h0a_contract -c 'CREATE EXTENSION postgis;'
    DATABASE_URL=postgresql://.../agrosat_h0a_contract python -m alembic upgrade head
    AGROSAT_TEST_DATABASE_URL=postgresql://.../agrosat_h0a_contract \
        python -m pytest tests/test_h0a_freshness_postgres_contract.py
"""

from __future__ import annotations

from datetime import date, timedelta
import os
import unittest
from urllib.parse import urlsplit

from services import observation_quality as quality


ISOLATED_PREFIX = "agrosat_h0a"
PRODUCTION_DATABASE = "agrosat"


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

POLYGON = (
    "POLYGON((64.400 39.700,64.410 39.700,64.410 39.710,64.400 39.710,64.400 39.700))"
)
SECONDARY_INDEX_CODES = ("savi", "evi", "ndmi", "ndre")
ALL_INDEX_CODES = ("ndvi",) + SECONDARY_INDEX_CODES


@unittest.skipIf(DATABASE_URL is None, "AGROSAT_TEST_DATABASE_URL is not set")
class ContractBase(unittest.TestCase):
    """Shared fixture: real database, real freshness code, real observations."""

    @classmethod
    def setUpClass(cls):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        cls.engine = create_engine(DATABASE_URL, future=True)
        cls.Session = sessionmaker(bind=cls.engine, autocommit=False, autoflush=False)
        # Fail loudly if the target is not migrated to the production head.
        with cls.Session() as session:
            revision = session.execute(
                __import__("sqlalchemy").text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        cls.revision = revision

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        from sqlalchemy import text

        self.session = self.Session()
        # Deterministic starting point inside the isolated database.
        # Children before parents: these tables carry foreign keys to each other.
        for table in (
            "operational_notification_events",
            "operational_notifications",
            "satellite_field_freshness",
            "satellite_collection_runs",
            "ndvi_records",
            "satellite_index_records",
            "fields",
            "users",
            "enterprises",
        ):
            self.session.execute(text(f"DELETE FROM {table}"))
        self.enterprise_id = self.session.execute(
            text(
                "INSERT INTO enterprises (name, code, is_active) "
                "VALUES ('H0A Test Enterprise', 'H0A', true) RETURNING id"
            )
        ).scalar_one()
        self.field_id = self.session.execute(
            text(
                "INSERT INTO fields (enterprise_id, name, code, geometry, is_active) "
                "VALUES (:enterprise_id, 'H0A Test Field', 'H0A-1', "
                "ST_SetSRID(ST_GeomFromText(:polygon), 4326), true) RETURNING id"
            ),
            {"enterprise_id": self.enterprise_id, "polygon": POLYGON},
        ).scalar_one()
        self.session.commit()

    def tearDown(self):
        self.session.rollback()
        self.session.close()

    # ─── helpers ────────────────────────────────────────────────────────────

    def _insert_ndvi(
        self,
        *,
        captured,
        mean_ndvi=0.62,
        valid_pixels_pct=100.0,
        cloud_cover_pct=None,
        satellite="Sentinel-2",
    ):
        from sqlalchemy import text

        record_id = self.session.execute(
            text(
                "INSERT INTO ndvi_records (field_id, captured_date, mean_ndvi, "
                "valid_pixels_pct, cloud_cover_pct, satellite) "
                "VALUES (:field_id, :captured, :mean_ndvi, :valid, :cloud, :satellite) "
                "RETURNING id"
            ),
            {
                "field_id": self.field_id,
                "captured": captured,
                "mean_ndvi": mean_ndvi,
                "valid": valid_pixels_pct,
                "cloud": cloud_cover_pct,
                "satellite": satellite,
            },
        ).scalar_one()
        self.session.commit()
        return record_id

    def _insert_index(
        self,
        *,
        index_code,
        captured,
        mean_value=0.48,
        valid_pixels_pct=100.0,
        cloud_cover_pct=None,
        satellite="Sentinel-2",
    ):
        from sqlalchemy import text

        record_id = self.session.execute(
            text(
                "INSERT INTO satellite_index_records (field_id, captured_date, "
                "index_code, mean_value, valid_pixels_pct, cloud_cover_pct, satellite) "
                "VALUES (:field_id, :captured, :index_code, :mean_value, :valid, "
                ":cloud, :satellite) RETURNING id"
            ),
            {
                "field_id": self.field_id,
                "captured": captured,
                "index_code": index_code,
                "mean_value": mean_value,
                "valid": valid_pixels_pct,
                "cloud": cloud_cover_pct,
                "satellite": satellite,
            },
        ).scalar_one()
        self.session.commit()
        return record_id

    def _seed_every_index(self, *, captured, valid_pixels_pct=100.0, cloud_cover_pct=None):
        """Seed all five indices, as one real collection cycle does.

        Freshness is tracked per (field, index), so a field seeded with NDVI
        alone legitimately has four NEVER_COLLECTED rows. Seeding the whole set
        keeps these assertions about the repair rather than about absent data.
        """
        self._insert_ndvi(
            captured=captured,
            valid_pixels_pct=valid_pixels_pct,
            cloud_cover_pct=cloud_cover_pct,
        )
        for index_code in ("savi", "evi", "ndmi", "ndre"):
            self._insert_index(
                index_code=index_code,
                captured=captured,
                valid_pixels_pct=valid_pixels_pct,
                cloud_cover_pct=cloud_cover_pct,
            )

    def _refresh(self, last_outcome=None):
        from services.autonomous_monitoring import ApplyRun, refresh_freshness

        return refresh_freshness(
            ApplyRun(session=self.session, run_id=None, run_key="h0a-test"),
            last_outcome=last_outcome,
        )

    def _freshness(self):
        from sqlalchemy import text

        rows = self.session.execute(
            text(
                "SELECT index_code, status, enterprise_id, last_accepted_scene "
                "FROM satellite_field_freshness WHERE field_id = :field_id"
            ),
            {"field_id": self.field_id},
        ).mappings().all()
        return {row["index_code"]: dict(row) for row in rows}


class FreshnessContractTests(ContractBase):
    """Production-shaped observations against the repaired contract."""

    # ─── the required contract proof ────────────────────────────────────────

    def test_database_is_at_the_production_revision(self):
        self.assertEqual(self.revision, "0016_operational_command_center")

    def test_production_shaped_observation_becomes_fresh(self):
        """The defect case: Sentinel-2, valid pixels 100, cloud metadata NULL."""
        today = date.today()
        self._insert_ndvi(captured=today, valid_pixels_pct=100.0, cloud_cover_pct=None)
        for code in SECONDARY_INDEX_CODES:
            self._insert_index(
                index_code=code,
                captured=today,
                valid_pixels_pct=100.0,
                cloud_cover_pct=None,
            )

        self._refresh()
        freshness = self._freshness()

        self.assertEqual(len(freshness), len(ALL_INDEX_CODES))
        for code in ALL_INDEX_CODES:
            with self.subTest(index_code=code):
                self.assertEqual(freshness[code]["status"], "FRESH")
                self.assertNotIn(
                    freshness[code]["status"],
                    {"NEVER_COLLECTED", "QUALITY_BLOCKED", "CLOUD_BLOCKED"},
                )
                self.assertIsNotNone(freshness[code]["last_accepted_scene"])

    def test_the_previous_predicate_would_have_rejected_the_same_row(self):
        """Pin the defect: the old NULL-hostile rule accepts nothing."""
        from sqlalchemy import text

        today = date.today()
        self._insert_ndvi(captured=today, valid_pixels_pct=100.0, cloud_cover_pct=None)

        old_rule = self.session.execute(
            text(
                "SELECT count(*) FROM ndvi_records WHERE field_id = :field_id "
                "AND mean_ndvi BETWEEN -1 AND 1 "
                "AND COALESCE(valid_pixels_pct,0) >= 60 "
                "AND COALESCE(cloud_cover_pct,101) <= 30"
            ),
            {"field_id": self.field_id},
        ).scalar_one()
        new_rule = self.session.execute(
            text(
                "SELECT count(*) FROM ndvi_records WHERE field_id = :field_id AND "
                + quality.accepted_observation_sql(
                    value_column="mean_ndvi",
                    minimum_valid_pixels_pct=quality.MIN_VALID_PIXELS_FRESHNESS_PCT,
                )
            ),
            {"field_id": self.field_id},
        ).scalar_one()

        self.assertEqual(old_rule, 0, "the old rule must reject the production shape")
        self.assertEqual(new_rule, 1, "the repaired rule must accept it")

    def test_tenant_identity_is_preserved(self):
        today = date.today()
        self._insert_ndvi(captured=today)
        self._refresh()
        for code, row in self._freshness().items():
            with self.subTest(index_code=code):
                self.assertEqual(row["enterprise_id"], self.enterprise_id)

    # ─── regressions: real quality filtering still applies ─────────────────

    def test_measured_high_cloud_is_still_rejected(self):
        self._insert_ndvi(
            captured=date.today(), valid_pixels_pct=100.0, cloud_cover_pct=80.0
        )
        self._refresh()
        self.assertEqual(self._freshness()["ndvi"]["status"], "NEVER_COLLECTED")

    def test_low_valid_pixels_is_still_rejected(self):
        self._insert_ndvi(
            captured=date.today(), valid_pixels_pct=10.0, cloud_cover_pct=None
        )
        self._refresh()
        self.assertEqual(self._freshness()["ndvi"]["status"], "NEVER_COLLECTED")

    def test_out_of_range_index_value_is_still_rejected(self):
        self._insert_ndvi(
            captured=date.today(), mean_ndvi=7.5, valid_pixels_pct=100.0
        )
        self._refresh()
        self.assertEqual(self._freshness()["ndvi"]["status"], "NEVER_COLLECTED")

    def test_malformed_cloud_metadata_is_still_rejected_in_sql(self):
        # Present but impossible. Must not be reinterpreted as "not measured".
        for cloud in (-5.0, 150.0, float("nan")):
            with self.subTest(cloud=cloud):
                self.setUp()
                self._insert_ndvi(
                    captured=date.today(),
                    valid_pixels_pct=100.0,
                    cloud_cover_pct=cloud,
                )
                self._refresh()
                self.assertEqual(
                    self._freshness()["ndvi"]["status"], "NEVER_COLLECTED"
                )

    def test_freshness_age_boundaries_are_unchanged(self):
        for offset, expected in ((0, "FRESH"), (10, "FRESH"), (15, "AGING"),
                                 (20, "AGING"), (25, "STALE")):
            with self.subTest(age_days=offset):
                self.setUp()
                self._insert_ndvi(captured=date.today() - timedelta(days=offset))
                self._refresh()
                self.assertEqual(self._freshness()["ndvi"]["status"], expected)

    def test_run_outcome_still_classifies_uncollected_fields(self):
        # No accepted observation at all: the run-level outcome still applies.
        self._refresh(last_outcome="provider_degraded")
        self.assertEqual(self._freshness()["ndvi"]["status"], "PROVIDER_DEGRADED")

    # ─── closed-loop contract ───────────────────────────────────────────────

    def test_null_cloud_row_is_an_acceptable_baseline_and_post_observation(self):
        from services import closed_loop_agronomy

        completed = date.today() - timedelta(days=30)
        baseline_date = completed - timedelta(days=2)
        post_date = completed + timedelta(days=8)

        baseline_id = self._insert_ndvi(
            captured=baseline_date, mean_ndvi=0.40, cloud_cover_pct=None
        )
        post_id = self._insert_ndvi(
            captured=post_date, mean_ndvi=0.62, cloud_cover_pct=None
        )

        baseline = closed_loop_agronomy.observation(
            self.session, self.field_id, before=completed, accepted=True
        )
        self.assertIsNotNone(baseline, "a NULL-cloud row must be an eligible baseline")
        self.assertEqual(baseline["id"], baseline_id)
        self.assertIsNone(baseline["cloud"])

        post = closed_loop_agronomy.observation(
            self.session,
            self.field_id,
            after=completed + timedelta(days=7),
            accepted=True,
        )
        self.assertIsNotNone(post, "a NULL-cloud row must be an eligible post scene")
        self.assertEqual(post["id"], post_id)

    def test_agronomy_policy_accepts_the_null_cloud_baseline(self):
        from services import agronomy_policy, closed_loop_agronomy

        self._insert_ndvi(captured=date.today(), cloud_cover_pct=None)
        baseline = closed_loop_agronomy.observation(
            self.session, self.field_id, before=date.today(), accepted=True
        )
        self.assertIsNotNone(baseline)
        self.assertIsNone(
            agronomy_policy.quality(baseline),
            "a real NULL-cloud observation must not be QUALITY_BLOCKED",
        )

    def test_secondary_index_snapshot_includes_null_cloud_rows(self):
        from sqlalchemy import text

        from services import closed_loop_agronomy

        for code in SECONDARY_INDEX_CODES:
            self._insert_index(index_code=code, captured=date.today())
        accepted = self.session.execute(
            text(
                "SELECT count(*) FROM satellite_index_records "
                "WHERE field_id = :field_id AND "
                + closed_loop_agronomy._ACCEPTED_INDEX_OBSERVATION
            ),
            {"field_id": self.field_id},
        ).scalar_one()
        self.assertEqual(accepted, len(SECONDARY_INDEX_CODES))

    # ─── reconciliation command ─────────────────────────────────────────────

    def test_reconciliation_dry_run_reports_without_writing(self):
        from sqlalchemy import text

        import scripts.recompute_satellite_freshness as command

        today = date.today()
        self._insert_ndvi(captured=today)
        # Seed the production defect shape: stored NEVER_COLLECTED.
        self._refresh(last_outcome=None)
        self.session.execute(
            text(
                "UPDATE satellite_field_freshness SET status='NEVER_COLLECTED', "
                "last_accepted_scene=NULL, last_accepted_at=NULL"
            )
        )
        self.session.commit()

        original = command.SessionLocal
        command.SessionLocal = self.Session
        try:
            code, summary = command.run(command.parse_args([]))
        finally:
            command.SessionLocal = original

        self.assertEqual(code, 0)
        self.assertEqual(summary["mode"], "dry-run")
        self.assertEqual(summary["provider_calls"], 0)
        self.assertEqual(summary["freshness_rows_written"], 0)
        self.assertGreater(summary["rows_changing"], 0)
        # Nothing was written.
        self.assertEqual(self._freshness()["ndvi"]["status"], "NEVER_COLLECTED")

    def test_reconciliation_apply_repairs_and_is_idempotent(self):
        from sqlalchemy import text

        import scripts.recompute_satellite_freshness as command

        self._insert_ndvi(captured=date.today())
        self._refresh(last_outcome=None)
        self.session.execute(
            text("UPDATE satellite_field_freshness SET status='NEVER_COLLECTED'")
        )
        self.session.commit()

        original = command.SessionLocal
        command.SessionLocal = self.Session
        try:
            first_code, first = command.run(command.parse_args(["--apply"]))
            second_code, second = command.run(command.parse_args(["--apply"]))
        finally:
            command.SessionLocal = original

        self.assertEqual((first_code, second_code), (0, 0))
        self.assertEqual(self._freshness()["ndvi"]["status"], "FRESH")
        # Idempotent: the second pass finds nothing left to change and,
        # crucially, writes no rows at all.
        self.assertEqual(second["rows_changing"], 0)
        self.assertEqual(second["freshness_rows_written"], 0)
        self.assertEqual(second["freshness_after"], first["freshness_after"])

    def test_reconciliation_refuses_a_non_isolated_target_name(self):
        with self.assertRaises(RuntimeError):
            os.environ["AGROSAT_TEST_DATABASE_URL"] = (
                "postgresql://user@127.0.0.1:5432/agrosat"
            )
            try:
                _isolated_database_url()
            finally:
                os.environ["AGROSAT_TEST_DATABASE_URL"] = DATABASE_URL

    def test_reconciliation_imports_no_provider_module(self):
        import ast
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1]
            / "scripts/recompute_satellite_freshness.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for forbidden in (
            "services.satellite",
            "services.satellite_indices",
            "services.satellite_collection",
            "services.sentinel_provider",
            "httpx",
            "requests",
            "sentinelhub",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, imported)


class RecoveryProvenanceTests(ContractBase):
    """Architect review of 81ff420: the manual recovery must be minimally mutating.

    The collector's refresh_freshness() rewrites every freshness row and stamps
    the run that produced it. Driving a manual recovery through it erased
    collector provenance and touched every row on every pass. These tests pin
    the dedicated recovery path's guarantees instead.
    """

    def _run_command(self, argv):
        import scripts.recompute_satellite_freshness as command

        original = command.SessionLocal
        command.SessionLocal = self.Session
        try:
            return command.run(command.parse_args(argv))
        finally:
            command.SessionLocal = original

    def _collection_run(self):
        """A real, completed collection run to attribute freshness rows to."""
        from sqlalchemy import text

        run_id = self.session.execute(
            text(
                "INSERT INTO satellite_collection_runs "
                "(id,run_key,mode,status,release_commit,rule_version,audit_identity,"
                " started_at,heartbeat_at,provider_status) "
                "VALUES (gen_random_uuid(),'h0a-seed-'||gen_random_uuid()::text,"
                " 'apply','succeeded',"
                " repeat('a',40),'r3-e-v1','h0a',now(),now(),'healthy') RETURNING id"
            )
        ).scalar_one()
        self.session.commit()
        return run_id

    def _row(self, index_code="ndvi"):
        from sqlalchemy import text

        return self.session.execute(
            text(
                "SELECT status,last_run_id,last_failure_reason,last_quality_reason,"
                "       last_attempted_scene,last_attempted_at,next_eligible_at,"
                "       last_accepted_scene,last_accepted_at,updated_at "
                "  FROM satellite_field_freshness WHERE index_code=:code"
            ),
            {"code": index_code},
        ).mappings().one()

    def _stage_damaged_row_with_provenance(self):
        """C1 damage on a row that carries real collector provenance."""
        from sqlalchemy import text

        run_id = self._collection_run()
        self._insert_ndvi(captured=date.today())
        self._refresh()
        self.session.execute(
            text(
                "UPDATE satellite_field_freshness SET "
                "  status='NEVER_COLLECTED', last_accepted_scene=NULL, "
                "  last_accepted_at=NULL, last_run_id=:run_id, "
                "  last_failure_reason='provider_degraded', "
                "  last_quality_reason='low_valid_pixels', "
                "  last_attempted_scene='scene-A', last_attempted_at=now(), "
                "  next_eligible_at=now() + interval '1 day' "
                "WHERE index_code='ndvi'"
            ),
            {"run_id": run_id},
        )
        self.session.commit()
        return run_id

    # ─── 1. preserve collector provenance ──────────────────────────────────

    def test_recovery_repairs_state_but_preserves_collector_provenance(self):
        run_id = self._stage_damaged_row_with_provenance()
        before = self._row()
        self.assertEqual(before["status"], "NEVER_COLLECTED")

        code, summary = self._run_command(["--apply"])
        self.assertEqual(code, 0, summary)

        after = self._row()
        # Repaired from the persisted observation.
        self.assertEqual(after["status"], "FRESH")
        self.assertIsNotNone(after["last_accepted_scene"])
        self.assertIsNotNone(after["last_accepted_at"])
        # Provenance the manual command has no authority over.
        self.assertEqual(after["last_run_id"], run_id)
        self.assertEqual(after["last_failure_reason"], before["last_failure_reason"])
        self.assertEqual(after["last_quality_reason"], before["last_quality_reason"])
        self.assertEqual(after["last_attempted_scene"], before["last_attempted_scene"])
        self.assertEqual(after["last_attempted_at"], before["last_attempted_at"])
        self.assertEqual(after["next_eligible_at"], before["next_eligible_at"])

    def test_recovery_invents_no_collection_run(self):
        self._stage_damaged_row_with_provenance()
        from sqlalchemy import text

        before = self.session.execute(
            text("SELECT count(*) FROM satellite_collection_runs")
        ).scalar_one()
        code, summary = self._run_command(["--apply"])
        self.assertEqual(code, 0, summary)
        self.assertEqual(
            self.session.execute(
                text("SELECT count(*) FROM satellite_collection_runs")
            ).scalar_one(),
            before,
        )
        self.assertEqual(summary["provider_calls"], 0)

    # ─── 2. physical idempotency ───────────────────────────────────────────

    def test_second_apply_writes_no_rows_and_does_not_move_updated_at(self):
        self._stage_damaged_row_with_provenance()
        first_code, first = self._run_command(["--apply"])
        self.assertEqual(first_code, 0, first)
        self.assertEqual(first["freshness_rows_written"], 1)

        settled = self._row()

        second_code, second = self._run_command(["--apply"])
        self.assertEqual(second_code, 0, second)
        self.assertEqual(second["rows_changing"], 0)
        self.assertEqual(second["freshness_rows_written"], 0)

        again = self._row()
        # Physically unchanged, not merely logically equivalent.
        self.assertEqual(again["updated_at"], settled["updated_at"])
        self.assertEqual(again["last_run_id"], settled["last_run_id"])
        self.assertEqual(dict(again), dict(settled))

    # ─── 3. absence of evidence is not evidence ────────────────────────────

    def test_recovery_does_not_reclassify_rows_without_an_accepted_observation(self):
        """A run-derived block must survive a manual recovery untouched."""
        from sqlalchemy import text

        run_id = self._collection_run()
        # NDVI has a real observation; the four secondary indices have none.
        self._insert_ndvi(captured=date.today())
        self._refresh()
        self.session.execute(
            text(
                "UPDATE satellite_field_freshness SET status='PROVIDER_DEGRADED', "
                "  last_run_id=:run_id, last_failure_reason='provider_degraded', "
                "  next_eligible_at=now() + interval '1 day' "
                "WHERE index_code <> 'ndvi'"
            ),
            {"run_id": run_id},
        )
        self.session.execute(
            text(
                "UPDATE satellite_field_freshness SET status='NEVER_COLLECTED', "
                "last_accepted_scene=NULL, last_accepted_at=NULL WHERE index_code='ndvi'"
            )
        )
        self.session.commit()
        blocked_before = {
            code: dict(self._row(code)) for code in ("savi", "evi", "ndmi", "ndre")
        }

        code, summary = self._run_command(["--apply"])
        self.assertEqual(code, 0, summary)

        # Only the row with an accepted observation was touched.
        self.assertEqual(summary["freshness_rows_written"], 1)
        self.assertEqual(self._row("ndvi")["status"], "FRESH")
        for index_code, before in blocked_before.items():
            with self.subTest(index_code=index_code):
                self.assertEqual(dict(self._row(index_code)), before)

    def test_blocked_statuses_survive_each_of_the_run_derived_kinds(self):
        from sqlalchemy import text

        # NDVI carries a real observation; SAVI deliberately carries none, so
        # only a run could have produced its status.
        self._insert_ndvi(captured=date.today())
        self._refresh()
        for status in ("PROVIDER_DEGRADED", "QUALITY_BLOCKED", "CLOUD_BLOCKED"):
            with self.subTest(status=status):
                self.session.execute(
                    text(
                        "UPDATE satellite_field_freshness SET status=:status "
                        "WHERE index_code='savi'"
                    ),
                    {"status": status},
                )
                self.session.commit()
                code, summary = self._run_command(["--apply"])
                self.assertEqual(code, 0, summary)
                self.assertEqual(self._row("savi")["status"], status)

    # ─── 4. exact mutation count ───────────────────────────────────────────

    def test_preview_and_apply_agree_on_the_exact_write_set(self):
        """N rows differ => preview rows_changing == N and apply writes N."""
        from sqlalchemy import text

        self._seed_every_index(captured=date.today())
        self._refresh()
        # Damage exactly three of the five rows.
        damaged = ("ndvi", "savi", "evi")
        self.session.execute(
            text(
                "UPDATE satellite_field_freshness SET status='NEVER_COLLECTED', "
                "last_accepted_scene=NULL, last_accepted_at=NULL "
                "WHERE index_code = ANY(:codes)"
            ),
            {"codes": list(damaged)},
        )
        self.session.commit()

        dry_code, dry = self._run_command([])
        self.assertEqual(dry_code, 0, dry)
        self.assertEqual(dry["rows_changing"], len(damaged))
        self.assertEqual(dry["freshness_rows_written"], 0)

        apply_code, applied = self._run_command(["--apply"])
        self.assertEqual(apply_code, 0, applied)
        self.assertEqual(applied["rows_changing"], len(damaged))
        self.assertEqual(applied["freshness_rows_written"], len(damaged))
        self.assertEqual(
            {row["status"] for row in self._freshness().values()}, {"FRESH"}
        )

    def test_recovery_creates_a_missing_row_for_an_accepted_observation(self):
        """A pair with observations but no freshness row is still repaired."""
        from sqlalchemy import text

        self._insert_ndvi(captured=date.today())
        self.assertEqual(
            self.session.execute(
                text("SELECT count(*) FROM satellite_field_freshness")
            ).scalar_one(),
            0,
        )
        code, summary = self._run_command(["--apply"])
        self.assertEqual(code, 0, summary)
        self.assertEqual(summary["freshness_rows_written"], 1)
        self.assertEqual(self._row("ndvi")["status"], "FRESH")
        # Absence of evidence stays absent: no rows invented for the other four.
        self.assertEqual(
            self.session.execute(
                text("SELECT count(*) FROM satellite_field_freshness")
            ).scalar_one(),
            1,
        )

    # ─── mutation surface ──────────────────────────────────────────────────

    def test_recovery_writes_no_observation_rows(self):
        from sqlalchemy import text

        self._stage_damaged_row_with_provenance()
        counts = lambda: tuple(  # noqa: E731
            self.session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            for table in ("ndvi_records", "satellite_index_records")
        )
        before = counts()
        code, summary = self._run_command(["--apply"])
        self.assertEqual(code, 0, summary)
        self.assertEqual(counts(), before)

    def test_recovery_refuses_while_a_collection_cycle_is_in_flight(self):
        from sqlalchemy import text

        self._stage_damaged_row_with_provenance()
        self.session.execute(
            text(
                "INSERT INTO satellite_collection_runs "
                "(id,run_key,mode,status,release_commit,rule_version,audit_identity,"
                " started_at,heartbeat_at,provider_status) "
                "VALUES (gen_random_uuid(),'h0a-inflight','apply','running',"
                " repeat('b',40),'r3-e-v1','h0a',now(),now(),'pending')"
            )
        )
        self.session.commit()

        code, summary = self._run_command(["--apply"])
        self.assertEqual(code, 3, summary)
        self.assertEqual(summary["failure_category"], "lock_contention")
        # And nothing was written.
        self.assertEqual(self._row("ndvi")["status"], "NEVER_COLLECTED")

    def test_apply_takes_the_lock_before_inspecting_state(self):
        """The in-flight check must run under the lock, not before it."""
        import inspect

        import scripts.recompute_satellite_freshness as command

        source = inspect.getsource(command.run)
        lock = source.index("pg_try_advisory_xact_lock")
        in_flight = source.index("satellite_collection_runs")
        preview = source.index("_transitions(session)", in_flight)
        write = source.index("apply_freshness_recovery", in_flight)
        self.assertLess(lock, in_flight, "lock must precede the in-flight check")
        self.assertLess(in_flight, preview, "in-flight check must precede the preview")
        self.assertLess(preview, write, "preview must precede the write")

    def test_recovery_does_not_call_the_collector_write_path(self):
        import ast
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1]
            / "scripts/recompute_satellite_freshness.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        # Prose may name the collector path; code must not import or call it.
        bound = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                bound.update(alias.asname or alias.name for alias in node.names)
            elif isinstance(node, ast.Name):
                bound.add(node.id)
        self.assertNotIn("refresh_freshness", bound)
        self.assertNotIn("ApplyRun", bound)
        self.assertIn("apply_freshness_recovery", bound)
        # Exactly one commit, in the apply branch.
        commits = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "commit"
        ]
        self.assertEqual(len(commits), 1, "apply must commit exactly once")

    def test_collector_refresh_path_is_unchanged_by_this_review_fix(self):
        """refresh_freshness still owns run stamping; recovery is separate."""
        from services.autonomous_monitoring import (
            _REFRESH_FRESHNESS_SQL,
            _RECOVERY_APPLY_SQL,
        )

        self.assertIn("last_run_id=excluded.last_run_id", _REFRESH_FRESHNESS_SQL)
        self.assertNotIn("last_run_id", _RECOVERY_APPLY_SQL)
        self.assertNotIn("last_failure_reason", _RECOVERY_APPLY_SQL)
        self.assertIn("IS DISTINCT FROM", _RECOVERY_APPLY_SQL)


class NotificationRecoveryTests(ContractBase):
    """Can the reconciler that already ships retire the false notifications?

    The brief forbids deleting the 1,375 production rows and forbids a blind
    cleanup script, and the notification worker is disabled in production.
    This proves the existing reconciler retires those rows by itself once
    freshness is healthy, so no manual deletion is needed.

    Both the creation and the retirement below run through the production code
    path (``reconcile_notifications``); nothing is hand-inserted, so the rows
    under test are shaped exactly like the production ones.
    """

    def _operator(self, *, role, email):
        from sqlalchemy import text

        user_id = self.session.execute(
            text(
                "INSERT INTO users (enterprise_id, email, hashed_password, full_name, "
                " role, is_active) VALUES (:enterprise_id, :email, 'x', :name, "
                " :role, true) RETURNING id"
            ),
            {
                "enterprise_id": self.enterprise_id,
                "email": email,
                "name": f"H0A {role}",
                "role": role,
            },
        ).scalar_one()
        self.session.commit()
        return user_id

    def _reconcile(self, *, apply, as_of=None):
        from datetime import datetime, timezone

        from services.operational_notifications import reconcile_notifications

        return reconcile_notifications(
            self.session, apply=apply, as_of=as_of or datetime.now(timezone.utc)
        )

    def _notifications(self, *, source_kind="freshness"):
        from sqlalchemy import text

        return self.session.execute(
            text(
                "SELECT id, status, version, resolved_at FROM operational_notifications "
                "WHERE source_kind = :source_kind ORDER BY id"
            ),
            {"source_kind": source_kind},
        ).mappings().all()

    def _stage_production_defect(self):
        """Reproduce production: a good observation, an unhealthy freshness row."""
        from sqlalchemy import text

        self._seed_every_index(captured=date.today())
        self._refresh()
        # This is what the NULL-hostile predicate left behind in production:
        # real observations present, every freshness row saying otherwise.
        self.session.execute(
            text(
                "UPDATE satellite_field_freshness SET status='NEVER_COLLECTED', "
                "last_accepted_scene=NULL, last_accepted_at=NULL "
                "WHERE field_id=:field_id"
            ),
            {"field_id": self.field_id},
        )
        self.session.commit()

    def test_false_freshness_notifications_are_resolved_not_deleted(self):
        self._operator(role="manager", email="h0a-manager@example.invalid")
        self._stage_production_defect()

        # 1. Production behaviour before the repair: the notification is created.
        created = self._reconcile(apply=True)
        self.assertEqual(created["created"], 5, created)
        rows = self._notifications()
        self.assertEqual(len(rows), 5, rows)
        notification_ids = [row["id"] for row in rows]
        self.assertEqual({row["status"] for row in rows}, {"unread"})

        # 2. While freshness is unhealthy the notifications are still
        #    justified, so the reconciler must NOT retire them.
        self.assertEqual(self._reconcile(apply=False)["would_resolve"], 0)

        # 3. The H0-A repair: recompute freshness from the stored observation.
        from sqlalchemy import text

        self._refresh()
        self.assertEqual(
            {row["status"] for row in self._freshness().values()},
            {"FRESH"},
        )

        # 4. Now the same reconciler sees every row as retirable.
        self.assertEqual(self._reconcile(apply=False)["would_resolve"], 5)
        applied = self._reconcile(apply=True)
        self.assertEqual(applied["resolved"], 5)

        # 5. Retirement is a versioned update with an audit event, not a delete.
        after = self._notifications()
        self.assertEqual(len(after), 5, "every row must still exist")
        self.assertEqual({row["status"] for row in after}, {"resolved"})
        self.assertTrue(all(row["resolved_at"] is not None for row in after))
        self.assertTrue(all(row["version"] > 1 for row in after))
        self.assertEqual(
            self.session.execute(
                text(
                    "SELECT count(*) FROM operational_notification_events "
                    "WHERE notification_id = ANY(:ids) AND event_type='resolved' "
                    "AND actor_key='system:reconciler' AND reason='source_not_actionable'"
                ),
                {"ids": notification_ids},
            ).scalar_one(),
            5,
        )

    def test_repaired_freshness_generates_no_new_notification(self):
        """A repaired field must not regenerate the alert on the next pass."""
        self._operator(role="manager", email="h0a-manager2@example.invalid")
        self._seed_every_index(captured=date.today())
        self._refresh()

        summary = self._reconcile(apply=True)
        self.assertEqual(len(self._notifications()), 0, summary)

    def test_genuinely_stale_freshness_still_notifies_after_the_repair(self):
        """The repair must not silence real staleness."""
        self._operator(role="manager", email="h0a-manager3@example.invalid")
        # 40 days old: beyond the STALE boundary, quality otherwise perfect.
        self._seed_every_index(captured=date.today() - timedelta(days=40))
        self._refresh()
        self.assertEqual(
            {row["status"] for row in self._freshness().values()}, {"STALE"}
        )

        self._reconcile(apply=True)
        rows = self._notifications()
        self.assertEqual(len(rows), 5, "real staleness must still raise notifications")
        self.assertEqual({row["status"] for row in rows}, {"unread"})
        # And they must stay open, because the source condition is real.
        self.assertEqual(self._reconcile(apply=False)["would_resolve"], 0)

    def test_retirement_predicate_keys_off_freshness_status_only(self):
        """Retirement must not depend on the disabled notification worker."""
        from services.operational_notifications import STALE_ACTIVE_SQL

        flat = " ".join(STALE_ACTIVE_SQL.split())
        self.assertIn(
            "n.notification_type='external_source_unavailable' "
            "AND n.source_kind='freshness'",
            flat,
        )
        self.assertIn("s.status<>'FRESH'", flat)

    def test_retirement_is_bounded_and_needs_repeated_passes(self):
        """1,375 rows cannot clear in one pass; document the real batch size."""
        from services.operational_notifications import DEFAULT_LIMIT, MAX_LIMIT

        self.assertEqual(DEFAULT_LIMIT, 200)
        self.assertEqual(MAX_LIMIT, 500)
        # Recorded so the runbook's pass count cannot drift from the code.
        self.assertEqual(-(-1375 // MAX_LIMIT), 3)
        self.assertEqual(-(-1375 // DEFAULT_LIMIT), 7)


if __name__ == "__main__":
    unittest.main()
