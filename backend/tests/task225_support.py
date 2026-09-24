"""Shared PostgreSQL harness for the TASK_225 closed-loop suites.

Not collected by pytest (no ``test_`` prefix). The guard is the one used by the
H0-A and TASK_223 suites: every class skips unless ``AGROSAT_TEST_DATABASE_URL``
is set, and any database whose name does not start with ``agrosat_h0a`` is
refused before a statement runs. The production database is named ``agrosat``.

    createdb agrosat_h0a_task225
    psql -d agrosat_h0a_task225 -c 'CREATE EXTENSION postgis;'
    DATABASE_URL=postgresql://.../agrosat_h0a_task225 python -m alembic upgrade head
    AGROSAT_TEST_DATABASE_URL=postgresql://.../agrosat_h0a_task225 \\
        python -m pytest tests/test_task225_*.py

Everything here goes through production code: the canonical collector steps,
the canonical inspection and agronomy services and the FastAPI application.
SQL written directly by the harness only seeds what a collector or an operator
would already have persisted (enterprises, fields, users, observations) and
reads state back for assertions.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import json
import os
import tempfile
from types import SimpleNamespace
import unittest
from urllib.parse import urlsplit

# Module level on purpose: with postponed annotations FastAPI resolves the
# override's ``request: Request`` hint against this module's globals.
from fastapi import Request


ISOLATED_PREFIX = "agrosat_h0a"
PRODUCTION_DATABASE = "agrosat"
RELEASE_COMMIT = "0" * 40
HEAD_REVISION = "0016_operational_command_center"


def _isolated_database_url() -> str | None:
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

# About 0.006 x 0.005 degrees near Bukhara: roughly 28 ha each.
FIELD_A = "POLYGON((64.400 39.700,64.406 39.700,64.406 39.705,64.400 39.705,64.400 39.700))"
FIELD_B = "POLYGON((64.500 39.700,64.506 39.700,64.506 39.705,64.500 39.705,64.500 39.700))"
FIELD_A_EDITED = "POLYGON((64.400 39.700,64.407 39.700,64.407 39.705,64.400 39.705,64.400 39.700))"
QUIET_FIELD = "POLYGON(({x} 39.800,{x2} 39.800,{x2} 39.804,{x} 39.804,{x} 39.800))"
INSIDE_A = (64.403, 39.7025)

# Truncated together, children and parents alike, so no suite leaves rows that
# another suite's DELETE would trip over. TRUNCATE does not fire the row-level
# immutability trigger on operational_audit_events.
DOMAIN_TABLES = (
    "operational_notification_events", "operational_notifications",
    "agronomy_events", "agronomy_verifications", "inspection_evidence",
    "agronomy_work_items", "agronomy_plans", "autonomous_anomaly_transitions",
    "autonomous_anomaly_candidates", "pixel_anomaly_inspections", "pixel_anomalies",
    "pixel_anomaly_runs", "operational_audit_events", "action_verification_requests",
    "corrective_actions", "inspection_results", "field_inspections",
    "satellite_field_freshness", "satellite_collection_runs", "ndvi_records",
    "satellite_index_records", "alerts", "crop_seasons", "fields", "users", "enterprises",
)

HISTORY = (0.62, 0.64, 0.63, 0.65, 0.66, 0.62, 0.64, 0.63, 0.65, 0.64)
JPEG = b"\xff\xd8\xff\xe0" + b"TASK225-evidence" * 8


def utc_midnight(value: date) -> datetime:
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


@unittest.skipIf(DATABASE_URL is None, "AGROSAT_TEST_DATABASE_URL is not set")
class Task225Base(unittest.TestCase):
    """Real database, real services, one tenant pair per test."""

    maxDiff = None

    @classmethod
    def setUpClass(cls):
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import sessionmaker

        cls.engine = create_engine(DATABASE_URL, future=True, pool_size=8, max_overflow=8)
        cls.Session = sessionmaker(bind=cls.engine, autocommit=False, autoflush=False)
        with cls.engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        if revision != HEAD_REVISION:
            raise RuntimeError(f"test database is at {revision}, expected {HEAD_REVISION}")

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    # ── lifecycle ───────────────────────────────────────────────────────────

    def setUp(self):
        from config import settings
        from services import anomaly_inspections, autonomous_monitoring, closed_loop_agronomy

        self._truncate()
        self.today = datetime.now(timezone.utc).date()
        self.clock_offset = timedelta(0)
        self.cache_invalidations = []
        self.media = tempfile.TemporaryDirectory(prefix="task225-media-")
        self._patches = [
            (autonomous_monitoring, "SessionLocal", self.Session),
            (autonomous_monitoring, "engine", self.engine),
            (anomaly_inspections, "cache_delete_patterns", self.cache_invalidations.append),
            (closed_loop_agronomy, "now", lambda: datetime.now(timezone.utc) + self.clock_offset),
            (settings, "inspection_media_directory", self.media.name),
        ]
        self._saved = [(owner, name, getattr(owner, name)) for owner, name, _ in self._patches]
        for owner, name, value in self._patches:
            setattr(owner, name, value)
        self.sessions = []
        self._seed()

    def tearDown(self):
        for session in self.sessions:
            try:
                session.close()
            except Exception:
                pass
        for owner, name, value in self._saved:
            setattr(owner, name, value)
        self._truncate()
        self.media.cleanup()

    def _truncate(self):
        from sqlalchemy import text

        with self.engine.begin() as connection:
            connection.execute(text(
                "TRUNCATE TABLE " + ",".join(DOMAIN_TABLES) + " RESTART IDENTITY CASCADE"
            ))

    def session(self):
        session = self.Session()
        self.sessions.append(session)
        return session

    def sql(self, statement, params=None):
        """Run one statement in its own committed transaction; return mappings."""
        from sqlalchemy import text

        with self.engine.begin() as connection:
            result = connection.execute(text(statement), params or {})
            return [dict(row) for row in result.mappings()] if result.returns_rows else []

    def scalar(self, statement, params=None):
        rows = self.sql(statement, params)
        return next(iter(rows[0].values())) if rows else None

    # ── seed ────────────────────────────────────────────────────────────────

    def _seed(self):
        self.enterprise_a = self.scalar(
            "INSERT INTO enterprises (name, code, is_active) VALUES ('T225 Alpha','T225A',true) RETURNING id")
        self.enterprise_b = self.scalar(
            "INSERT INTO enterprises (name, code, is_active) VALUES ('T225 Beta','T225B',true) RETURNING id")
        self.field_a = self._field(self.enterprise_a, "T225 Field A", FIELD_A)
        self.field_b = self._field(self.enterprise_b, "T225 Field B", FIELD_B)
        # The automatic-promotion spike guard compares triggered fields with all
        # active fields; production has 275. Quiet fields keep one real signal
        # well under the 10% guard, as in production.
        for index in range(10):
            x = 64.600 + index * 0.01
            self._field(self.enterprise_a, f"T225 Quiet {index}",
                        QUIET_FIELD.format(x=f"{x:.3f}", x2=f"{x + 0.004:.3f}"))
        self.admin = self._user(None, "admin", "t225-admin")
        self.manager_a = self._user(self.enterprise_a, "manager", "t225-manager-a")
        self.agronomist_a = self._user(self.enterprise_a, "agronomist", "t225-agronomist-a")
        self.viewer_a = self._user(self.enterprise_a, "viewer", "t225-viewer-a")
        self.manager_b = self._user(self.enterprise_b, "manager", "t225-manager-b")
        self.agronomist_b = self._user(self.enterprise_b, "agronomist", "t225-agronomist-b")

    def _field(self, enterprise_id, name, polygon):
        return self.scalar(
            "INSERT INTO fields (enterprise_id, name, code, geometry, area_ha, is_active) "
            "VALUES (:enterprise, :name, :code, ST_SetSRID(ST_GeomFromText(:polygon),4326), "
            "ST_Area(ST_SetSRID(ST_GeomFromText(:polygon),4326)::geography)/10000.0, true) RETURNING id",
            {"enterprise": enterprise_id, "name": name, "code": name[-8:], "polygon": polygon},
        )

    def _user(self, enterprise_id, role, email):
        user_id = self.scalar(
            "INSERT INTO users (enterprise_id, email, hashed_password, full_name, role, is_active) "
            "VALUES (:enterprise, :email, 'x', :name, :role, true) RETURNING id",
            {"enterprise": enterprise_id, "email": f"{email}@example.invalid",
             "name": email.upper(), "role": role},
        )
        return SimpleNamespace(id=user_id, role=role, enterprise_id=enterprise_id, is_active=True)

    # ── observations, as the canonical collector persists them ─────────────

    def ndvi(self, field_id, captured, value, *, valid=100.0, cloud=None):
        return self.scalar(
            "INSERT INTO ndvi_records (field_id, captured_date, mean_ndvi, valid_pixels_pct, "
            "cloud_cover_pct, satellite) VALUES (:field, :captured, :value, :valid, :cloud, 'Sentinel-2') "
            "RETURNING id",
            {"field": field_id, "captured": captured, "value": value, "valid": valid, "cloud": cloud},
        )

    def index(self, field_id, code, captured, value, *, valid=100.0, cloud=None):
        return self.scalar(
            "INSERT INTO satellite_index_records (field_id, captured_date, index_code, mean_value, "
            "valid_pixels_pct, cloud_cover_pct, satellite) VALUES (:field, :captured, :code, :value, "
            ":valid, :cloud, 'Sentinel-2') RETURNING id",
            {"field": field_id, "captured": captured, "code": code, "value": value,
             "valid": valid, "cloud": cloud},
        )

    def history_dates(self):
        """Ten baseline scenes, then two recent ones, all inside the lookback."""
        baseline = [self.today - timedelta(days=74 - 6 * step) for step in range(10)]
        return baseline, [self.today - timedelta(days=8), self.today - timedelta(days=2)]

    def seed_persistent_drop(self, field_id, *, drops=(0.30, 0.28), supporting=("savi", "evi", "ndmi")):
        """A persistent, multi-index NDVI drop in accepted observations."""
        baseline_dates, recent = self.history_dates()
        records = [self.ndvi(field_id, day, value) for day, value in zip(baseline_dates, HISTORY)]
        drop_ids = [self.ndvi(field_id, day, value) for day, value in zip(recent, drops)]
        for code in ("savi", "evi", "ndmi", "ndre"):
            for day, value in zip(baseline_dates, HISTORY):
                self.index(field_id, code, day, round(value - 0.2, 4))
            self.index(field_id, code, recent[-1], 0.05 if code in supporting else round(HISTORY[0] - 0.2, 4))
        return records, drop_ids

    def seed_quiet_history(self, field_id):
        baseline_dates, recent = self.history_dates()
        for day, value in zip(baseline_dates + recent, HISTORY + (0.63, 0.64)):
            self.ndvi(field_id, day, value)

    # ── canonical collector cycle ───────────────────────────────────────────

    @contextmanager
    def collection_run(self, key):
        from services import autonomous_monitoring

        run = autonomous_monitoring.begin_apply_run(
            run_key=key.ljust(64, "0")[:64], release_commit=RELEASE_COMMIT,
            audit_identity="TASK_225_TEST",
        )
        try:
            yield run
        finally:
            autonomous_monitoring.finish_apply_run(
                run, exit_code=0, provider_status="healthy", counters={}, failure_category=None,
            )

    def run_monitoring_cycle(self, key="task225-cycle"):
        """Detection then promotion, exactly as collect_satellite.py sequences them."""
        from services import autonomous_monitoring

        with self.collection_run(key) as run:
            detection = autonomous_monitoring.detect_observation_candidates(
                run, as_of=datetime.now(timezone.utc),
            )
            promotion = autonomous_monitoring.reconcile_pixel_candidates(run)
        return detection, promotion

    # ── HTTP client over the real application ───────────────────────────────

    def client(self, user):
        """A TestClient that authenticates as ``user`` on every request.

        dependency_overrides is one dict per application, so the identity rides
        on a per-client header instead: several clients can act side by side.
        """
        from fastapi.testclient import TestClient

        from api.auth import get_current_active_user
        from database import get_db
        from main import app

        self._identities = getattr(self, "_identities", {})
        self._identities[str(user.id)] = user

        def db():
            session = self.Session()
            try:
                yield session
            finally:
                session.close()

        def authenticated(request: Request):
            return self._identities[request.headers["X-Task225-User"]]

        app.dependency_overrides[get_db] = db
        app.dependency_overrides[get_current_active_user] = authenticated
        self.addCleanup(app.dependency_overrides.clear)
        return TestClient(app, headers={"X-Task225-User": str(user.id)})

    # ── reads for assertions ────────────────────────────────────────────────

    def inspection(self, inspection_id):
        rows = self.sql(
            "SELECT i.*, ST_AsGeoJSON(i.source_zone)::json AS zone_geojson, "
            "ST_Equals(ST_Multi(i.source_zone), ST_Multi(f.geometry)) AS zone_is_field "
            "FROM field_inspections i JOIN fields f ON f.id=i.field_id WHERE i.id=:id",
            {"id": inspection_id},
        )
        return rows[0] if rows else None

    def plan(self, plan_id):
        rows = self.sql("SELECT * FROM agronomy_plans WHERE id=:id", {"id": plan_id})
        return rows[0] if rows else None

    def plan_events(self, plan_id):
        return [row["event_type"] for row in self.sql(
            "SELECT event_type FROM agronomy_events WHERE plan_id=:id ORDER BY id", {"id": plan_id})]

    def audit_events(self, inspection_id):
        return self.sql(
            "SELECT event_type, event_metadata FROM operational_audit_events "
            "WHERE inspection_id=:id ORDER BY id", {"id": inspection_id})

    def count(self, table, where="true", params=None):
        return self.scalar(f"SELECT count(*) FROM {table} WHERE {where}", params)

    def snapshot(self, *tables):
        """Row images of whole tables, to prove a call changed nothing."""
        return {table: self.sql(f"SELECT * FROM {table} ORDER BY 1") for table in tables}

    @staticmethod
    def dumps(value):
        return json.dumps(value, sort_keys=True, default=str)
