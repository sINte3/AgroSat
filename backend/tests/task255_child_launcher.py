"""TASK_255: run the real multi-index collector child against an isolated database.

Not collected by pytest. A batch runner test replaces the child command
``[python, <backend>/scripts/collect_satellite_indices.py, ARGS...]`` with
``[python, <this file>, <backend>/scripts/collect_satellite_indices.py, ARGS...]``.
This process then runs the unmodified ``collect_satellite_indices.main()`` with
exactly ARGS, and with only test-process injections:

* ``DATABASE_URL`` is the isolated test database (``AGROSAT_TEST_DATABASE_URL``,
  name ``agrosat_h0a*``, never ``agrosat``); its identity is checked before the
  child runs and appended to ``AGROSAT_TASK255_IDENTITY_LOG`` when set.
* No network: every Python socket connection outside loopback raises, and the
  provider service is ``FixtureMultiIndexService`` (records from
  ``AGROSAT_TASK255_FIXTURE``). The real-provenance write guard still runs.
* The collector mutex is a test-only name, so a production collector run on this
  host can never contend with a test child.
* ``AGROSAT_TASK255_FAULTS`` (JSON list) makes PostgreSQL fail at chosen keys;
  ``AGROSAT_TASK255_FAULT_EVENTS`` records each injected fault.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit

BACKEND = Path(__file__).resolve().parents[1]
CHILD = (BACKEND / "scripts" / "collect_satellite_indices.py").resolve()
TEST_MUTEX = "Global\\AgroSat_TASK255_TestCollector_v1"


def _refuse(message: str) -> None:
    print(f"TASK255 LAUNCHER REFUSED: {message}", file=sys.stderr)
    raise SystemExit(2)


def _isolated_url() -> str:
    url = (os.environ.get("AGROSAT_TEST_DATABASE_URL") or "").strip()
    name = urlsplit(url).path.lstrip("/")
    if not url or name == "agrosat" or not name.startswith("agrosat_h0a"):
        _refuse("an isolated agrosat_h0a* test database is required")
    return url


def forced_mutex_lock(acquire_lock, mutex_name: str = TEST_MUTEX):
    """``acquire_lock`` that always takes a test-only mutex, whatever the caller passes.

    The collector lock is a Win32 named mutex. Off Windows (the Linux PostgreSQL CI
    lane) the test lock takes no mutex and only returns the lock path; each test
    drives one child at a time.
    """

    def acquire(*args, **kwargs):
        if os.name != "nt":
            return kwargs.get("lock_file") or (args[0] if args else None)
        kwargs["mutex_name"] = mutex_name
        return acquire_lock(*args, **kwargs)

    return acquire


def _block_network() -> None:
    real_connect = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        if not loopback:
            raise ConnectionRefusedError("TASK_255 test child: network is disabled")
        return real_connect(self, address)

    socket.socket.connect = guarded


def main() -> None:
    if len(sys.argv) < 2 or Path(sys.argv[1]).resolve() != CHILD:
        _refuse("only the real collect_satellite_indices.py child may be launched")
    if os.environ.get("AGROSAT_RUNTIME_ENV_FILE") or (BACKEND / ".env").exists():
        _refuse("a runtime .env would mix production settings into the test child")
    os.environ["DATABASE_URL"] = _isolated_url()
    # Placeholder credentials so the unchanged mode validation runs; no service that
    # could use them is ever constructed and no socket may leave loopback.
    os.environ["SENTINEL_HUB_CLIENT_ID"] = "task255-fixture-client"
    os.environ["SENTINEL_HUB_CLIENT_SECRET"] = "task255-fixture-placeholder"
    _block_network()
    sys.path.insert(0, str(BACKEND))
    sys.path.insert(0, str(BACKEND / "tests"))

    from sqlalchemy import text

    import database
    from scripts import collect_satellite_indices as collector
    from services import collector_locking
    from task255_support import faulty_sessionmaker, fixture_from_file

    with database.engine.connect() as connection:
        name, port = connection.execute(text("SELECT current_database(), inet_server_port()")).one()
    expected_port = os.environ.get("AGROSAT_TASK255_EXPECTED_PORT")
    if name == "agrosat" or not name.startswith("agrosat_h0a") or (expected_port and str(port) != expected_port):
        _refuse("the connected database is not the isolated test database")
    identity_log = os.environ.get("AGROSAT_TASK255_IDENTITY_LOG")
    if identity_log:
        with open(identity_log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"pid": os.getpid(), "current_database": name, "server_port": port}) + "\n")

    fixture = fixture_from_file(os.environ["AGROSAT_TASK255_FIXTURE"])
    collector.get_multi_index_satellite_service = lambda: fixture
    collector.acquire_lock = forced_mutex_lock(collector_locking.acquire_lock)
    collector._invalidate_observation_cache = lambda enterprise_id: True

    faults = json.loads(os.environ.get("AGROSAT_TASK255_FAULTS") or "[]")
    if faults:
        events: list = []
        collector.SessionLocal = faulty_sessionmaker(database.engine, faults, events=events)
        events_log = os.environ.get("AGROSAT_TASK255_FAULT_EVENTS")
        if events_log:
            import atexit

            def flush():
                with open(events_log, "a", encoding="utf-8") as handle:
                    for event in events:
                        handle.write(json.dumps({"pid": os.getpid(), **event}) + "\n")

            atexit.register(flush)

    sys.argv = [str(CHILD), *sys.argv[2:]]
    collector.main()


if __name__ == "__main__":
    main()
