"""Isolated security tests for the administrator recovery CLI."""

from __future__ import annotations

import argparse
import ast
import io
import json
import shutil
import tempfile
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import manage_admin_credentials as tool


STRONG = "Synthetic_Task175_Str0ng!"
OTHER_STRONG = "Synthetic_Task175_0ther!"
OLD_HASH = "bcrypt-old-hash"


class FakeColumn:
    def __eq__(self, other):
        return ("eq", other)


class FakeUserModel:
    email = FakeColumn()


class FakeFunc:
    @staticmethod
    def btrim(value):
        return value

    @staticmethod
    def lower(value):
        return value


class FakePasswordContext:
    def verify(self, password, hashed):
        if hashed.startswith("generated:"):
            return hashed == "generated:" + password
        return password == OTHER_STRONG and hashed == OLD_HASH

    def hash(self, password):
        return "generated:" + password

    def identify(self, hashed):
        return "bcrypt" if hashed else None


class FakeScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class FakeQuery:
    def __init__(self, session):
        self.session = session

    def filter(self, expression):
        self.session.filter_expression = expression
        return self

    def with_for_update(self):
        self.session.lock_requested = True
        return self

    def limit(self, value):
        self.session.limit_value = value
        return self

    def all(self):
        return self.session.users[: self.session.limit_value]


class FakeSession:
    def __init__(self, users, database="agrosat", on_commit=None):
        self.users = users
        self.database = database
        self.on_commit = on_commit
        self.lock_requested = False
        self.filter_expression = None
        self.limit_value = 2
        self.query_count = 0
        self.execute_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0

    def execute(self, statement):
        self.execute_count += 1
        return FakeScalarResult(self.database)

    def query(self, model):
        self.query_count += 1
        return FakeQuery(self)

    def commit(self):
        self.commit_count += 1
        if self.on_commit:
            self.on_commit(self)

    def rollback(self):
        self.rollback_count += 1

    def close(self):
        self.close_count += 1


class FailingQuerySession(FakeSession):
    def query(self, model):
        raise RuntimeError("synthetic post-commit read failure")


class SessionFactory:
    def __init__(self, sessions):
        self.sessions = list(sessions)
        self.created = []

    def __call__(self):
        if not self.sessions:
            raise AssertionError("Unexpected database session")
        session = self.sessions.pop(0)
        self.created.append(session)
        return session


def make_user(**overrides):
    values = {
        "id": 7,
        "email": "admin@agrosat.uz",
        "role": "admin",
        "is_active": True,
        "enterprise_id": 3,
        "full_name": "Existing Administrator",
        "phone": "+998000000000",
        "created_at": datetime(2024, 1, 2, 3, 4, 5),
        "last_login": datetime(2025, 6, 7, 8, 9, 10),
        "hashed_password": OLD_HASH,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_runtime(sessions):
    return tool.Runtime(
        SessionLocal=SessionFactory(sessions),
        User=FakeUserModel,
        pwd_context=FakePasswordContext(),
        text=lambda value: value,
        func=FakeFunc(),
    )


def make_args(audit_dir, **overrides):
    values = {
        "command": "reset-password",
        "email": "  ADMIN@AGROSAT.UZ  ",
        "expected_user_id": 7,
        "expected_database": "agrosat",
        "reason": "Emergency admin access recovery after failed login",
        "audit_dir": str(audit_dir),
        "prompt": True,
        "verify_api_base": "http://127.0.0.1:8000",
        "activate": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def success_http(method, url, **kwargs):
    if url.endswith("/health"):
        return 200, {"status": "ok"}
    if url.endswith("/api/auth/login"):
        return 200, {"access_token": "synthetic-memory-token"}
    if url.endswith("/api/auth/me"):
        return 200, {
            "id": 7,
            "email": "admin@agrosat.uz",
            "role": "admin",
            "is_active": True,
        }
    raise AssertionError(url)


class ParserAndPasswordTests(unittest.TestCase):
    def test_parser_has_no_password_option_or_environment_mode(self):
        parser = tool.build_parser()
        options = {
            option
            for action in parser._subparsers._group_actions[0].choices[
                "reset-password"
            ]._actions
            for option in action.option_strings
        }
        self.assertNotIn("--password", options)
        self.assertFalse(any("env" in option.casefold() for option in options))
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "reset-password",
                    "--password",
                    "not-accepted",
                ]
            )

    def test_password_is_collected_by_exactly_two_masked_prompts(self):
        with patch.object(tool.getpass, "getpass", side_effect=[STRONG, STRONG]) as masked:
            self.assertEqual(tool.collect_password(), STRONG)
        self.assertEqual(masked.call_count, 2)

    def test_confirmation_mismatch_rejected_without_value_in_error(self):
        with patch.object(tool.getpass, "getpass", side_effect=[STRONG, OTHER_STRONG]):
            with self.assertRaises(tool.RecoveryError) as caught:
                tool.collect_password()
        self.assertEqual(caught.exception.code, tool.EXIT_INVALID_INPUT)
        self.assertNotIn(STRONG, str(caught.exception))
        self.assertNotIn(OTHER_STRONG, str(caught.exception))

    def test_full_password_policy_rejects_each_invalid_class(self):
        cases = (
            "",
            "   ",
            " " + STRONG,
            STRONG + " ",
            "Short1!",
            "synthetic_task175_1!",
            "SYNTHETIC_TASK175_1!",
            "Synthetic_Task_No_Digit!",
            "SyntheticTask175NoSpecial1",
            "password",
            "Change-Me-Password-123!",
        )
        for candidate in cases:
            with self.subTest(candidate_length=len(candidate)):
                with self.assertRaises(tool.RecoveryError):
                    tool.validate_password(candidate)

    def test_known_bootstrap_digest_is_rejected_without_plaintext_constant(self):
        candidate = STRONG
        digest = tool.hashlib.sha256(candidate.encode("utf-8")).hexdigest()
        with patch.object(tool, "_KNOWN_SEED_PASSWORD_SHA256", digest):
            with self.assertRaises(tool.RecoveryError):
                tool.validate_password(candidate)

    def test_strong_password_is_accepted(self):
        self.assertEqual(tool.validate_password(STRONG), STRONG)


class InspectTests(unittest.TestCase):
    def _args(self, **overrides):
        values = {
            "email": " ADMIN@AGROSAT.UZ ",
            "expected_database": "agrosat",
            "json": True,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_inspect_is_read_only_and_sanitized(self):
        user = make_user()
        session = FakeSession([user])
        payload = tool.inspect_account(self._args(), make_runtime([session]))
        self.assertEqual(payload["database_name"], "agrosat")
        self.assertEqual(payload["email"], "admin@agrosat.uz")
        self.assertTrue(payload["exactly_one_match"])
        self.assertTrue(payload["password_hash_scheme_supported"])
        self.assertEqual(session.commit_count, 0)
        self.assertEqual(session.rollback_count, 1)
        self.assertFalse(session.lock_requested)
        serialized = json.dumps(payload)
        self.assertNotIn(OLD_HASH, serialized)

    def test_database_mismatch_blocks_before_user_query(self):
        session = FakeSession([make_user()], database="other")
        with self.assertRaises(tool.RecoveryError) as caught:
            tool.inspect_account(self._args(), make_runtime([session]))
        self.assertEqual(caught.exception.code, tool.EXIT_DATABASE_IDENTITY)
        self.assertEqual(session.query_count, 0)
        self.assertEqual(session.commit_count, 0)

    def test_zero_and_multiple_matches_are_reported_without_row_details(self):
        for users, count in (([], 0), ([make_user(), make_user(id=8)], 2)):
            with self.subTest(count=count):
                payload = tool.inspect_account(
                    self._args(), make_runtime([FakeSession(users)])
                )
                self.assertEqual(payload["match_count"], count)
                self.assertFalse(payload["exactly_one_match"])
                self.assertIsNone(payload["user_id"])


class ResetTests(unittest.TestCase):
    def setUp(self):
        self.temp_path = (
            Path("C:/AgroSat_backups/task175_admin_recovery")
            / ("unit_" + uuid.uuid4().hex)
        )
        self.temp_path.mkdir(parents=True)
        self.audit_dir = self.temp_path / "audit"
        self.tool_commit_patch = patch.object(tool, "_tool_commit", return_value="a" * 40)
        self.tool_commit_patch.start()

    def tearDown(self):
        self.tool_commit_patch.stop()
        shutil.rmtree(self.temp_path, ignore_errors=True)

    def run_reset(self, sessions, *, args=None, prompt=STRONG, http=success_http):
        runtime = make_runtime(sessions)
        args = args or make_args(self.audit_dir)
        with patch.object(tool, "collect_password", return_value=prompt), patch.object(
            tool, "_http_json", side_effect=http
        ):
            result = tool.reset_password(args, runtime)
        return result, runtime

    def test_zero_and_multiple_match_block_before_write(self):
        for index, users in enumerate(([], [make_user(), make_user(id=8)])):
            with self.subTest(count=len(users)):
                audit_dir = self.temp_path / f"audit_{index}"
                session = FakeSession(users)
                with self.assertRaises(tool.RecoveryError) as caught:
                    self.run_reset(
                        [session], args=make_args(audit_dir)
                    )
                self.assertEqual(caught.exception.code, tool.EXIT_TARGET_IDENTITY)
                self.assertEqual(session.commit_count, 0)
                self.assertTrue(session.lock_requested)

    def test_wrong_user_id_and_non_admin_role_block(self):
        cases = (
            (make_user(id=8), tool.EXIT_TARGET_IDENTITY),
            (make_user(role="viewer"), tool.EXIT_TARGET_IDENTITY),
        )
        for index, (user, code) in enumerate(cases):
            with self.subTest(index=index):
                session = FakeSession([user])
                with self.assertRaises(tool.RecoveryError) as caught:
                    self.run_reset(
                        [session],
                        args=make_args(self.temp_path / f"identity_{index}"),
                    )
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(session.commit_count, 0)
                self.assertTrue(session.lock_requested)

    def test_database_mismatch_blocks_before_row_query_and_write(self):
        session = FakeSession([make_user()], database="other")
        with self.assertRaises(tool.RecoveryError) as caught:
            self.run_reset([session])
        self.assertEqual(caught.exception.code, tool.EXIT_DATABASE_IDENTITY)
        self.assertEqual(session.query_count, 0)
        self.assertEqual(session.commit_count, 0)

    def test_success_changes_only_hash_and_explicit_activation(self):
        user = make_user(is_active=False)
        preserved = {
            name: getattr(user, name)
            for name in (
                "id",
                "email",
                "role",
                "enterprise_id",
                "full_name",
                "phone",
                "created_at",
                "last_login",
            )
        }
        update = FakeSession([user])
        verify = FakeSession([user])
        (audit, evidence), runtime = self.run_reset([update, verify])
        self.assertTrue(update.lock_requested)
        self.assertEqual(update.commit_count, 1)
        self.assertEqual(user.hashed_password, "generated:" + STRONG)
        self.assertTrue(user.is_active)
        self.assertTrue(audit["activation_changed"])
        self.assertTrue(audit["password_hash_changed"])
        self.assertEqual(audit["result"], "PASS")
        for name, value in preserved.items():
            self.assertEqual(getattr(user, name), value)
        self.assertEqual(len(runtime.SessionLocal.created), 2)
        self.assertTrue(all(path.exists() for path in evidence))

    def test_activation_is_unchanged_without_explicit_switch(self):
        user = make_user(is_active=False)
        args = make_args(self.audit_dir, activate=False)

        def inactive_http(method, url, **kwargs):
            status, payload = success_http(method, url, **kwargs)
            if url.endswith("/api/auth/me"):
                payload["is_active"] = False
            return status, payload

        (audit, _), _ = self.run_reset(
            [FakeSession([user]), FakeSession([user])],
            args=args,
            http=inactive_http,
        )
        self.assertFalse(user.is_active)
        self.assertFalse(audit["activation_changed"])

    def test_idempotent_password_does_not_generate_new_hash(self):
        user = make_user()
        pwd_context = FakePasswordContext()
        runtime = tool.Runtime(
            SessionLocal=SessionFactory([FakeSession([user]), FakeSession([user])]),
            User=FakeUserModel,
            pwd_context=pwd_context,
            text=lambda value: value,
            func=FakeFunc(),
        )
        with patch.object(tool, "collect_password", return_value=OTHER_STRONG), patch.object(
            tool, "_http_json", side_effect=success_http
        ), patch.object(pwd_context, "hash", wraps=pwd_context.hash) as hash_call:
            audit, _ = tool.reset_password(make_args(self.audit_dir), runtime)
        hash_call.assert_not_called()
        self.assertEqual(user.hashed_password, OLD_HASH)
        self.assertFalse(audit["password_hash_changed"])
        self.assertTrue(audit["database_hash_verified"])

    def test_api_health_failure_blocks_before_database_session(self):
        runtime = make_runtime([])

        def failed_health(method, url, **kwargs):
            return 503, {}

        with patch.object(tool, "collect_password", return_value=STRONG), patch.object(
            tool, "_http_json", side_effect=failed_health
        ):
            with self.assertRaises(tool.RecoveryError) as caught:
                tool.reset_password(make_args(self.audit_dir), runtime)
        self.assertEqual(caught.exception.code, tool.EXIT_API_PREFLIGHT)
        self.assertEqual(runtime.SessionLocal.created, [])

    def test_login_failure_restores_previous_values(self):
        user = make_user(is_active=False)

        def login_failure(method, url, **kwargs):
            if url.endswith("/health"):
                return 200, {"status": "ok"}
            if url.endswith("/api/auth/login"):
                return 401, {}
            raise AssertionError(url)

        sessions = [FakeSession([user]) for _ in range(4)]
        with self.assertRaises(tool.RecoveryError) as caught:
            self.run_reset(sessions, http=login_failure)
        self.assertEqual(caught.exception.code, tool.EXIT_VERIFICATION_ROLLED_BACK)
        self.assertEqual(user.hashed_password, OLD_HASH)
        self.assertFalse(user.is_active)
        audit = self.read_audit_json()
        self.assertTrue(audit["rollback_attempted"])
        self.assertTrue(audit["rollback_succeeded"])
        self.assertFalse(audit["api_login_verified"])

    def test_me_mismatch_restores_previous_values(self):
        user = make_user()

        def mismatch_http(method, url, **kwargs):
            status, payload = success_http(method, url, **kwargs)
            if url.endswith("/api/auth/me"):
                payload["id"] = 999
            return status, payload

        sessions = [FakeSession([user]) for _ in range(4)]
        with self.assertRaises(tool.RecoveryError) as caught:
            self.run_reset(sessions, http=mismatch_http)
        self.assertEqual(caught.exception.code, tool.EXIT_VERIFICATION_ROLLED_BACK)
        self.assertEqual(user.hashed_password, OLD_HASH)
        audit = self.read_audit_json()
        self.assertTrue(audit["api_login_verified"])
        self.assertFalse(audit["api_me_verified"])
        self.assertTrue(audit["rollback_succeeded"])

    def test_concurrent_change_prevents_unsafe_rollback(self):
        user = make_user()

        def mismatch_http(method, url, **kwargs):
            status, payload = success_http(method, url, **kwargs)
            if url.endswith("/api/auth/me"):
                payload["role"] = "viewer"
                user.hashed_password = "concurrent-hash"
            return status, payload

        sessions = [FakeSession([user]) for _ in range(3)]
        with self.assertRaises(tool.RecoveryError) as caught:
            self.run_reset(sessions, http=mismatch_http)
        self.assertEqual(caught.exception.code, tool.EXIT_CRITICAL_ROLLBACK)
        self.assertEqual(user.hashed_password, "concurrent-hash")
        audit = self.read_audit_json()
        self.assertTrue(audit["rollback_attempted"])
        self.assertFalse(audit["rollback_succeeded"])

    def test_concurrent_activation_change_prevents_unsafe_rollback(self):
        user = make_user(is_active=False)

        def mismatch_http(method, url, **kwargs):
            status, payload = success_http(method, url, **kwargs)
            if url.endswith("/api/auth/me"):
                payload["id"] = 999
                user.is_active = False
            return status, payload

        sessions = [FakeSession([user]) for _ in range(3)]
        with self.assertRaises(tool.RecoveryError) as caught:
            self.run_reset(sessions, http=mismatch_http)
        self.assertEqual(caught.exception.code, tool.EXIT_CRITICAL_ROLLBACK)
        self.assertFalse(user.is_active)
        audit = self.read_audit_json()
        self.assertTrue(audit["rollback_attempted"])
        self.assertFalse(audit["rollback_succeeded"])

    def test_unexpected_post_commit_read_failure_restores_previous_values(self):
        user = make_user(is_active=False)
        sessions = [
            FakeSession([user]),
            FailingQuerySession([user]),
            FakeSession([user]),
            FakeSession([user]),
        ]
        with self.assertRaises(tool.RecoveryError) as caught:
            self.run_reset(sessions)
        self.assertEqual(caught.exception.code, tool.EXIT_VERIFICATION_ROLLED_BACK)
        self.assertEqual(user.hashed_password, OLD_HASH)
        self.assertFalse(user.is_active)
        audit = self.read_audit_json()
        self.assertTrue(audit["rollback_succeeded"])

    def test_audit_contains_allowed_metadata_and_no_secrets(self):
        user = make_user()
        (audit, evidence), _ = self.run_reset(
            [FakeSession([user]), FakeSession([user])]
        )
        self.assertEqual(set(audit), set(tool._AUDIT_FIELDS))
        self.assertEqual(audit["reason"], make_args(self.audit_dir).reason)
        self.assertTrue(audit["hostname"])
        self.assertTrue(audit["operator_os_user"])
        combined = "\n".join(path.read_text(encoding="utf-8") for path in evidence)
        for secret in (STRONG, "generated:" + STRONG, "synthetic-memory-token", OLD_HASH):
            self.assertNotIn(secret, combined)
        self.assertNotIn("verify_api_base", combined)

    def test_audit_write_failure_has_distinct_exit_code(self):
        user = make_user()
        with patch.object(tool, "_write_audit", side_effect=tool.RecoveryError(
            tool.EXIT_AUDIT_WRITE, "audit failure"
        )):
            with self.assertRaises(tool.RecoveryError) as caught:
                self.run_reset([FakeSession([user]), FakeSession([user])])
        self.assertEqual(caught.exception.code, tool.EXIT_AUDIT_WRITE)

    def read_audit_json(self):
        json_files = list(self.audit_dir.glob("*.json"))
        self.assertEqual(len(json_files), 1)
        return json.loads(json_files[0].read_text(encoding="utf-8"))


class StaticSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source_path = Path(tool.__file__)
        cls.source = cls.source_path.read_text(encoding="utf-8")

    def test_no_public_endpoint_or_disallowed_operational_subsystem(self):
        lowered = self.source.casefold()
        self.assertNotIn("apirouter", lowered)
        self.assertNotIn("@router", lowered)
        self.assertNotIn("apscheduler", lowered)
        self.assertNotIn("alembic", lowered)
        self.assertNotIn("collector", lowered)
        self.assertNotIn("backfill", lowered)

    def test_source_has_no_password_environment_or_dotenv_access(self):
        self.assertNotIn("os.environ", self.source)
        self.assertNotIn("getenv(", self.source)
        self.assertNotIn(".env", self.source)
        self.assertNotIn("--password", self.source)

    def test_source_does_not_log_password_hash_token_or_database_url(self):
        lowered = self.source.casefold()
        self.assertNotIn("database_url", lowered)
        self.assertNotIn("logger.", lowered)
        self.assertNotIn("logging.", lowered)
        self.assertNotIn("print(password", lowered)
        self.assertNotIn("print(token", lowered)
        self.assertNotIn("print(written_hash", lowered)

    def test_no_lazy_loaded_relationship_is_accessed(self):
        tree = ast.parse(self.source)
        attributes = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        self.assertTrue({"enterprise", "fields", "alerts"}.isdisjoint(attributes))

    def test_source_contains_no_hardcoded_operational_password(self):
        self.assertNotIn(STRONG, self.source)
        self.assertNotIn(OTHER_STRONG, self.source)
        self.assertNotIn("AgroSat" + "2024!", self.source)

    def test_cli_errors_do_not_echo_prompted_secret(self):
        stderr = io.StringIO()
        directory = (
            Path("C:/AgroSat_backups/task175_admin_recovery")
            / ("unit_" + uuid.uuid4().hex)
        )
        directory.mkdir(parents=True)
        try:
            with patch.object(tool, "collect_password", side_effect=tool.RecoveryError(
                tool.EXIT_INVALID_INPUT, "Credential confirmation did not match."
            )), patch.object(tool, "_load_runtime", return_value=make_runtime([])), patch.object(
                tool, "_tool_commit", return_value="a" * 40
            ), redirect_stderr(stderr), redirect_stdout(io.StringIO()):
                code = tool.main(
                    [
                        "reset-password",
                        "--email",
                        "admin@agrosat.uz",
                        "--expected-user-id",
                        "7",
                        "--expected-database",
                        "agrosat",
                        "--reason",
                        "recovery",
                        "--audit-dir",
                        str(directory),
                        "--prompt",
                        "--verify-api-base",
                        "http://127.0.0.1:8000",
                    ]
                )
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        self.assertEqual(code, tool.EXIT_INVALID_INPUT)
        self.assertNotIn(STRONG, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
