"""TASK_255 test support: injected provider fixtures and real database failures.

Not collected by pytest (no ``test_`` prefix). Nothing here reaches a provider:

* ``FixtureMultiIndexService`` answers ``collect_indices_flat`` from records the
  test declares. It is the real service class (real provenance identity, so the
  collector's ``require_real_service`` guard runs unchanged) with its network
  methods replaced, and it is injected only into test processes.
* ``faulty_sessionmaker`` returns sessions that make PostgreSQL itself fail at a
  chosen point of one key's write: a ``READ ONLY`` transaction rejects the
  INSERT (SQLSTATE 25006), or the session's own backend is terminated before
  COMMIT. Only the backend that session owns is ever terminated.
"""

from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from services.satellite_collection import MultiIndexSentinelHubService

INDICES = ("savi", "evi", "ndmi", "ndre")
MEANS = {"savi": 0.41, "evi": 0.37, "ndmi": 0.22, "ndre": 0.29}


def flat_record(code: str, day: date, **overrides: Any) -> dict:
    """One field-level flat record exactly as the real parser shapes it."""
    record = {
        "index_code": code,
        "captured_date": day.isoformat(),
        "mean_value": MEANS[code],
        "min_value": MEANS[code] - 0.2,
        "max_value": MEANS[code] + 0.2,
        "std_value": 0.05,
        "p10_value": MEANS[code] - 0.1,
        "p90_value": MEANS[code] + 0.1,
        "valid_pixels_pct": 96.0,
        "cloud_cover_pct": 3.0,
        "interval_from": f"{day.isoformat()}T00:00:00Z",
        "interval_to": f"{(day + timedelta(days=1)).isoformat()}T00:00:00Z",
        "interval_ambiguous": False,
        "field_samples_accepted": True,
        "field_samples_reason": "accepted",
    }
    record.update(overrides)
    return record


class FixtureMultiIndexService(MultiIndexSentinelHubService):
    """The real multi-index service class with its provider calls replaced.

    ``days`` are the acquisition dates every field "returns"; ``overrides`` maps
    an index code to record overrides (for example a quality-blocked value).
    """

    def __init__(self, days: list[date] | None = None, overrides: dict | None = None, error: Exception | None = None):
        # Deliberately no super().__init__: no credentials, no provider endpoints.
        self.days = list(days or [])
        self.overrides = dict(overrides or {})
        self.error = error
        self.calls: list[dict] = []
        self._last_response_data = None

    def collect_indices_flat(self, geometry_wkt, index_codes, date_from, date_to, aggregation_interval="P1D"):
        self.calls.append({"index_codes": list(index_codes), "date_from": date_from, "date_to": date_to})
        if self.error is not None:
            raise self.error
        return [flat_record(code, day, **self.overrides.get(code, {}))
                for day in self.days if date_from <= day <= date_to for code in index_codes]

    def get_last_intervals_metadata(self, index_codes):
        return []

    def _provider_post(self, *args, **kwargs):  # never reached; a network call is a test failure
        raise AssertionError("TASK_255 fixture service must not call a provider")


def fixture_from_file(path: str | Path) -> FixtureMultiIndexService:
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    return FixtureMultiIndexService(days=[date.fromisoformat(item) for item in spec.get("days", [])],
                                    overrides=spec.get("overrides", {}))


# ── real PostgreSQL failures at one key's write ──────────────────────────────

def _matches(fault: dict, params: Any) -> bool:
    if not isinstance(params, dict):
        return False
    return (params.get("fid") == fault.get("field_id")
            and params.get("ic") == fault.get("index_code")
            and (fault.get("captured_date") is None or str(params.get("cd")) == fault["captured_date"]))


def _is_write(statement: Any) -> bool:
    return str(statement).lstrip().upper().startswith("INSERT INTO SATELLITE_INDEX_RECORDS")


def _is_delete(statement: Any) -> bool:
    return str(statement).lstrip().upper().startswith("DELETE FROM SATELLITE_INDEX_RECORDS")


def faulty_sessionmaker(engine, faults: list[dict], admin_engine=None, events: list | None = None):
    """Sessions on ``engine`` that make PostgreSQL fail for the faulted keys.

    fault kinds:
      ``insert_read_only``  -- the transaction is switched to READ ONLY right before
                               the key's INSERT: PostgreSQL rejects it (25006).
      ``delete_read_only``  -- the same, right before the key's --overwrite DELETE.
      ``commit_terminate``  -- the INSERT succeeds, then the session's own backend is
                               terminated before COMMIT: the server never commits.
      ``commit_lost_reply`` -- the COMMIT succeeds, then the client reports a lost
                               connection (the ambiguous-commit case).
    """
    events = events if events is not None else []
    admin_engine = admin_engine or engine

    class FaultySession(Session):
        _armed: dict | None = None

        def execute(self, statement, params=None, *args, **kwargs):
            if _is_delete(statement):
                for fault in faults:
                    if fault["kind"] == "delete_read_only" and _matches(fault, params):
                        events.append({"kind": fault["kind"], "field_id": params["fid"], "index_code": params["ic"]})
                        super().execute(text("SET TRANSACTION READ ONLY"))
            if _is_write(statement):
                for fault in faults:
                    if fault["kind"] != "delete_read_only" and _matches(fault, params):
                        events.append({"kind": fault["kind"], "field_id": params["fid"], "index_code": params["ic"]})
                        if fault["kind"] == "insert_read_only":
                            super().execute(text("SET TRANSACTION READ ONLY"))
                        else:
                            self._armed = fault
            return super().execute(statement, params, *args, **kwargs)

        def commit(self):
            fault, self._armed = self._armed, None
            if fault is None:
                return super().commit()
            if fault["kind"] == "commit_terminate":
                pid = super().execute(text("SELECT pg_backend_pid()")).scalar()
                with admin_engine.connect() as admin:
                    admin.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
                    for _ in range(100):
                        alive = admin.execute(text("SELECT count(*) FROM pg_stat_activity WHERE pid = :pid"),
                                              {"pid": pid}).scalar()
                        if not alive:
                            break
                        time.sleep(0.05)
                return super().commit()
            if fault["kind"] == "commit_lost_reply":
                super().commit()
                import psycopg2
                from sqlalchemy.exc import OperationalError

                raise OperationalError("COMMIT", {}, psycopg2.OperationalError("server closed the connection"))
            raise AssertionError(f"unknown fault {fault['kind']}")

    return sessionmaker(bind=engine, class_=FaultySession, autocommit=False, autoflush=False)
