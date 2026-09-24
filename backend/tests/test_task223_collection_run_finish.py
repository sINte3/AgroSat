"""TASK_223: finishing a run must not destroy the reason it ended.

``finish_apply_run`` used to release the advisory lock from a bare ``finally``.
When the status write failed, the transaction was already aborted, so the
unlock raised too — and that second exception replaced the first. The cycle
reported ``pg_advisory_unlock`` as its cause, the real failure was lost, and
the run row stayed ``running``.

These tests need no database: they drive the function with a session that
fails where production failed.
"""

from __future__ import annotations

import unittest

from services import autonomous_monitoring
from services.autonomous_monitoring import ApplyRun, finish_apply_run


class FakeResult:
    def scalar(self):
        return True

    def fetchall(self):
        return []


class FakeSession:
    """Records every call and fails exactly where it is told to."""

    def __init__(self, *, fail_on=(), fail_commit_on=()):
        self.fail_on = tuple(fail_on)
        self.fail_commit_on = tuple(fail_commit_on)
        self.calls: list[tuple[str, str]] = []
        self.closed = False
        self._last_sql = ""

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self._last_sql = sql
        self.calls.append(("execute", sql))
        for fragment, error in self.fail_on:
            if fragment in sql:
                raise error
        return FakeResult()

    def commit(self):
        self.calls.append(("commit", self._last_sql))
        for fragment, error in self.fail_commit_on:
            if fragment in self._last_sql:
                raise error
        return None

    def rollback(self):
        self.calls.append(("rollback", ""))
        return None

    def close(self):
        self.calls.append(("close", ""))
        self.closed = True

    # -- assertions helpers ---------------------------------------------

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.calls]

    def executed(self) -> list[str]:
        return [sql for kind, sql in self.calls if kind == "execute"]

    def index_of_execute(self, fragment: str) -> int:
        for position, (kind, sql) in enumerate(self.calls):
            if kind == "execute" and fragment in sql:
                return position
        raise AssertionError(f"no statement containing {fragment!r} was executed")

    def index_of_rollback(self) -> int:
        for position, (kind, _) in enumerate(self.calls):
            if kind == "rollback":
                return position
        raise AssertionError("no rollback was issued")


class FakeConnection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


COUNTERS_WRITE = "counters=CAST"
TERMINAL_WRITE = "status='running'"
UNLOCK = "pg_advisory_unlock"


def _run(session, connection=None):
    return ApplyRun(
        session=session,
        run_id="27db375c-d94d-4ee4-b96e-60d5e7e19383",
        run_key="task223",
        connection=connection,
    )


class FinishKeepsTheOriginalFailure(unittest.TestCase):
    def test_the_write_failure_is_raised_not_the_unlock_failure(self):
        primary = RuntimeError("current transaction is aborted")
        session = FakeSession(
            fail_on=(
                (COUNTERS_WRITE, primary),
                (UNLOCK, RuntimeError("pg_advisory_unlock failed too")),
            )
        )
        with self.assertRaises(RuntimeError) as raised:
            finish_apply_run(
                _run(session),
                exit_code=1,
                provider_status="degraded",
                counters={"success_count": 1},
                failure_category=None,
            )
        self.assertIs(raised.exception, primary)

    def test_a_release_failure_surfaces_when_the_write_succeeded(self):
        release_failure = RuntimeError("unlock failed")
        session = FakeSession(fail_on=((UNLOCK, release_failure),))
        with self.assertRaises(RuntimeError) as raised:
            finish_apply_run(
                _run(session),
                exit_code=0,
                provider_status="healthy",
                counters={},
                failure_category=None,
            )
        self.assertIs(raised.exception, release_failure)

    def test_a_clean_finish_raises_nothing(self):
        session = FakeSession()
        connection = FakeConnection()
        finish_apply_run(
            _run(session, connection),
            exit_code=0,
            provider_status="healthy",
            counters={},
            failure_category=None,
        )
        self.assertTrue(session.closed)
        self.assertTrue(connection.closed)


class FinishNeverLeavesTheRunRunning(unittest.TestCase):
    def test_a_failed_write_still_records_a_terminal_status(self):
        session = FakeSession(
            fail_on=((COUNTERS_WRITE, RuntimeError("current transaction is aborted")),)
        )
        with self.assertRaises(RuntimeError):
            finish_apply_run(
                _run(session),
                exit_code=4,
                provider_status="degraded",
                counters={"success_count": 1},
                failure_category="operational",
            )
        terminal = [sql for sql in session.executed() if TERMINAL_WRITE in sql]
        self.assertEqual(
            len(terminal),
            1,
            "a failed status write must be followed by one guarded terminal write",
        )
        self.assertNotIn(
            "counters=CAST",
            terminal[0],
            "the retry must not resend the payload that may have caused the failure",
        )
        self.assertIn(
            "status=:status",
            terminal[0],
            "the retry must still record the terminal status",
        )

    def test_the_rollback_precedes_the_unlock(self):
        session = FakeSession(
            fail_on=((COUNTERS_WRITE, RuntimeError("current transaction is aborted")),)
        )
        with self.assertRaises(RuntimeError):
            finish_apply_run(
                _run(session),
                exit_code=4,
                provider_status="degraded",
                counters={},
                failure_category="operational",
            )
        self.assertLess(
            session.index_of_rollback(),
            session.index_of_execute(UNLOCK),
            "an aborted transaction rejects the unlock unless it is rolled back first",
        )

    def test_the_connection_is_released_even_when_everything_fails(self):
        session = FakeSession(
            fail_on=(
                (COUNTERS_WRITE, RuntimeError("write failed")),
                (TERMINAL_WRITE, RuntimeError("retry failed")),
                (UNLOCK, RuntimeError("unlock failed")),
            )
        )
        connection = FakeConnection()
        with self.assertRaises(RuntimeError):
            finish_apply_run(
                _run(session, connection),
                exit_code=4,
                provider_status="degraded",
                counters={},
                failure_category="operational",
            )
        self.assertTrue(session.closed, "the session must be closed")
        self.assertTrue(connection.closed, "the pinned connection must be returned")


class RunOwnsItsConnection(unittest.TestCase):
    def test_apply_run_carries_the_connection_it_locked_on(self):
        fields = autonomous_monitoring.ApplyRun.__dataclass_fields__
        self.assertIn(
            "connection",
            fields,
            "the run must carry the connection holding its advisory lock",
        )

    def test_begin_binds_the_session_to_one_connection(self):
        import inspect

        source = inspect.getsource(autonomous_monitoring.begin_apply_run)
        self.assertIn(
            "engine.connect()",
            source,
            "the run must check out its own connection",
        )
        self.assertIn(
            "SessionLocal(bind=connection)",
            source,
            "the run's session must be bound to that connection, not to the pool",
        )


if __name__ == "__main__":
    unittest.main()
