"""TASK_238: the non-admin account CLI against a real PostgreSQL schema.

Guarded like every PostgreSQL suite (task225_support): skipped unless
``AGROSAT_TEST_DATABASE_URL`` names an isolated ``agrosat_h0a*`` database. The
CLI runs with the application's User model, the production bcrypt context and
real transactions, and sign-in is proven through the FastAPI login endpoint.
Harness SQL only seeds enterprises and pre-existing accounts and reads state.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from dotenv import dotenv_values

from task225_support import DATABASE_URL, HEAD_REVISION


def _private_directory(parent: Path) -> Path:
    path = Path(tempfile.mkdtemp(prefix="cred-", dir=parent)).resolve()
    if os.name == "nt":
        sid = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True,
                             errors="replace", check=True).stdout.strip().split(",")[-1].strip().strip('"')
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", "*S-1-5-18:(OI)(CI)F",
                        "*S-1-5-32-544:(OI)(CI)F", f"*{sid}:(OI)(CI)F"],
                       capture_output=True, text=True, errors="replace", check=True)
    else:
        os.chmod(path, 0o700)
    return path


def _credential(path: Path) -> tuple[str, str]:
    values = {key: value for key, value in dotenv_values(path).items() if value is not None}
    return values["AGROSAT_LOGIN_USERNAME"], values["AGROSAT_LOGIN_PASSWORD"]


class _RefusingVerify:
    """The production context, except that post-commit verification fails."""

    def __init__(self, context):
        self.context = context

    def hash(self, password):
        return self.context.hash(password)

    def verify(self, password, hashed):
        return False


@unittest.skipIf(DATABASE_URL is None, "AGROSAT_TEST_DATABASE_URL is not set")
class UserAccountsPostgresTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        from sqlalchemy import create_engine, text
        from sqlalchemy.engine import make_url
        from sqlalchemy.orm import sessionmaker

        cls.engine = create_engine(DATABASE_URL, future=True, pool_size=6, max_overflow=6)
        cls.Session = sessionmaker(bind=cls.engine, autocommit=False, autoflush=False)
        cls.database_name = make_url(DATABASE_URL).database
        with cls.engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        if revision != HEAD_REVISION:
            raise RuntimeError(f"test database is at {revision}, expected {HEAD_REVISION}")

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        from sqlalchemy import text

        from api.auth import pwd_context
        from config import settings
        from models.monitoring import User
        from scripts import manage_user_accounts as tool
        from services.migration_head import expected_migration_head

        self.tool = tool
        self.pwd_context = pwd_context
        self._truncate()
        self.alpha = self.scalar("INSERT INTO enterprises (name, code, is_active) VALUES ('T238 Alpha','T238A',true) RETURNING id")
        self.beta = self.scalar("INSERT INTO enterprises (name, code, is_active) VALUES ('T238 Beta','T238B',true) RETURNING id")
        self.closed = self.scalar("INSERT INTO enterprises (name, code, is_active) VALUES ('T238 Closed','T238C',false) RETURNING id")
        self.admin = self.seed_user("admin@t238.example", "Администратор AgroSat", "admin", None)
        self.saidov = self.seed_user("agronom@t238.example", "Умид Саидов", "viewer", self.beta)
        self.root = Path(tempfile.mkdtemp(prefix="t238-pg-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.credentials = _private_directory(self.root)
        self.audit_dir = self.root / "audit"
        self.runtime = tool.Runtime(
            open_store=lambda: tool.SqlStore(self.Session(), User, text),
            pwd_context=pwd_context,
            environment="test",
            expected_head=lambda: expected_migration_head().revision,
        )
        secret_key = patch.object(settings, "secret_key", secrets.token_urlsafe(48))
        secret_key.start()
        self.addCleanup(secret_key.stop)

    def tearDown(self):
        self._truncate()

    # ── database helpers ────────────────────────────────────────────────────

    def _truncate(self):
        from sqlalchemy import text

        with self.engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE users, enterprises RESTART IDENTITY CASCADE"))

    def sql(self, statement, params=None):
        from sqlalchemy import text

        with self.engine.begin() as connection:
            result = connection.execute(text(statement), params or {})
            return [dict(row) for row in result.mappings()] if result.returns_rows else []

    def scalar(self, statement, params=None):
        rows = self.sql(statement, params)
        return next(iter(rows[0].values())) if rows else None

    def seed_user(self, email, full_name, role, enterprise_id, *, password=None):
        hashed = self.pwd_context.hash(password) if password else "not-a-usable-hash"
        return self.scalar(
            "INSERT INTO users (email, full_name, role, enterprise_id, hashed_password, is_active, created_at) "
            "VALUES (:email, :name, :role, :enterprise, :hash, true, now() AT TIME ZONE 'UTC') RETURNING id",
            {"email": email, "name": full_name, "role": role, "enterprise": enterprise_id, "hash": hashed},
        )

    def snapshot(self):
        return self.sql(
            "SELECT (SELECT md5(coalesce(string_agg(u::text, '|' ORDER BY u.id), '')) FROM users u) AS users_md5, "
            "(SELECT count(*) FROM users) AS users, "
            "(SELECT last_value FROM users_id_seq) AS sequence_value, "
            "(SELECT is_called FROM users_id_seq) AS sequence_called"
        )[0]

    def row(self, login):
        rows = self.sql("SELECT * FROM users WHERE lower(btrim(email)) = :login ORDER BY id", {"login": login})
        return rows

    # ── command helpers ─────────────────────────────────────────────────────

    def create_args(self, login, full_name, role, enterprise_id, credential, **overrides):
        values = {
            "command": "create", "login": login, "full_name": full_name, "role": role,
            "enterprise_id": enterprise_id, "distinct_from": [], "expected_database": self.database_name,
            "credential_file": str(self.credentials / credential), "reason": "TASK_238 PostgreSQL qualification",
            "audit_dir": str(self.audit_dir), "verify_api_base": None, "apply": True,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def reset_args(self, login, user_id, credential, **overrides):
        values = {
            "command": "reset-password", "login": login, "expected_user_id": user_id,
            "expected_database": self.database_name, "credential_file": str(self.credentials / credential),
            "reason": "TASK_238 PostgreSQL qualification", "audit_dir": str(self.audit_dir),
            "verify_api_base": None, "apply": True,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def main(self, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(self.tool, "_load_runtime", return_value=self.runtime), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = self.tool.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def audit_text(self):
        if not self.audit_dir.exists():
            return ""
        return "".join(path.read_text(encoding="utf-8") for path in sorted(self.audit_dir.iterdir()))

    # ── the real application ────────────────────────────────────────────────

    def client(self):
        from fastapi.testclient import TestClient

        from database import get_db
        from main import app

        def db():
            session = self.Session()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = db
        self.addCleanup(app.dependency_overrides.pop, get_db, None)
        return TestClient(app)

    def sign_in(self, login, password):
        return self.client().post("/api/auth/login", data={"username": login, "password": password})

    def who_am_i(self, token):
        return self.client().get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    # ── create ──────────────────────────────────────────────────────────────

    def test_manager_and_agronomists_are_created_and_can_sign_in(self):
        people = (
            ("manager.t238@t238.example", "Байбутаев Умид Шавкатович", "manager", self.beta, "manager.env"),
            ("ivanov.t238@t238.example", "Иванов Иван", "agronomist", self.beta, "ivanov.env"),
            ("yashkin.t238@t238.example", "Яшкин Василий", "agronomist", self.alpha, "yashkin.env"),
        )
        for login, name, role, enterprise, file_name in people:
            with self.subTest(role=role, login=login):
                code, stdout, stderr = self.main([
                    "create", "--login", login.upper(), "--full-name", f"  {name} ", "--role", role,
                    "--enterprise-id", str(enterprise), "--expected-database", self.database_name,
                    "--credential-file", str(self.credentials / file_name), "--reason", "TASK_238 qualification",
                    "--audit-dir", str(self.audit_dir), "--apply",
                ])
                self.assertEqual(code, 0, stderr)
                rows = self.row(login)
                self.assertEqual(len(rows), 1)
                row = rows[0]
                self.assertEqual((row["email"], row["full_name"], row["role"], row["enterprise_id"], row["is_active"]),
                                 (login, name, role, enterprise, True))
                self.assertIsNone(row["phone"])
                self.assertIsNone(row["last_login"])
                self.assertIsNotNone(row["created_at"])
                self.assertTrue(row["hashed_password"].startswith("$2"))
                username, secret = _credential(self.credentials / file_name)
                self.assertEqual(username, login)
                self.assertIsNone(self.tool._privacy_problem(self.credentials / file_name, directory=False))
                combined = stdout + stderr + self.audit_text()
                self.assertNotIn(secret, combined)
                self.assertNotIn(row["hashed_password"], combined)
                signed_in = self.sign_in(login, secret)
                self.assertEqual(signed_in.status_code, 200, signed_in.text)
                me = self.who_am_i(signed_in.json()["access_token"])
                self.assertEqual(me.status_code, 200, me.text)
                self.assertEqual((me.json()["id"], me.json()["role"], me.json()["enterprise_id"], me.json()["is_active"]),
                                 (row["id"], role, enterprise, True))

    def test_dry_runs_and_inspect_write_nothing(self):
        agronomist = self.seed_user("petrov.t238@t238.example", "Петров Пётр", "agronomist", self.alpha,
                                    password=secrets.token_urlsafe(18))
        before = self.snapshot()
        create = self.tool.run_create(self.create_args("ivanov.t238@t238.example", "Иванов Иван", "agronomist",
                                                       self.beta, "ivanov.env", apply=False, audit_dir=None),
                                      self.runtime)
        self.assertEqual((create.exit_code, create.preflight["plan"]), (0, "CREATE"))
        target = create.preflight["database_target"]
        self.assertEqual((target["database_name"], target["alembic_revision"], target["revision_match"]),
                         (self.database_name, HEAD_REVISION, True))
        reset = self.tool.run_reset(self.reset_args("petrov.t238@t238.example", agronomist, "petrov.env",
                                                    apply=False, audit_dir=None), self.runtime)
        self.assertEqual((reset.exit_code, reset.preflight["plan"]), (0, "RESET"))
        code, stdout, _ = self.main(["inspect", "--login", "PETROV.T238@t238.example", "--full-name", "Пётр Петров",
                                     "--expected-database", self.database_name])
        self.assertEqual(code, 0)
        report = json.loads(stdout)
        self.assertEqual([m["id"] for m in report["login_matches"]], [agronomist])
        self.assertEqual(report["exact_login_match"], [agronomist])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(self.credentials.iterdir()), [])
        self.assertFalse(self.audit_dir.exists())

    def test_rerun_detects_the_existing_account_without_writing(self):
        args = self.create_args("ivanov.t238@t238.example", "Иванов Иван", "agronomist", self.beta, "ivanov.env")
        self.assertEqual(self.tool.run_create(args, self.runtime).exit_code, 0)
        before = self.snapshot()
        for credential in ("ivanov.env", "ivanov-again.env"):
            with self.subTest(credential=credential):
                outcome = self.tool.run_create(argparse.Namespace(**{
                    **vars(args), "credential_file": str(self.credentials / credential)}), self.runtime)
                self.assertEqual((outcome.exit_code, outcome.result["outcome"]), (0, "NO_CHANGE_ALREADY_EXISTS"))
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.credentials / "ivanov-again.env").exists())

    def test_exact_and_case_insensitive_login_collisions_are_refused(self):
        self.seed_user("Case.User@T238.example", "Случай Пользователь", "viewer", self.alpha)
        before = self.snapshot()
        cases = (
            ("case", "case.user@t238.example", "Другой Человек"),
            ("exact", "admin@t238.example", "Сидоров Сидор"),
        )
        for label, login, name in cases:
            with self.subTest(case=label):
                outcome = self.tool.run_create(self.create_args(login, name, "agronomist", self.alpha,
                                                                f"{label}.env"), self.runtime)
                self.assertEqual(outcome.exit_code, self.tool.EXIT_CONFLICT)
                self.assertFalse((self.credentials / f"{label}.env").exists())
        self.assertEqual(self.snapshot(), before)

    def test_same_person_needs_a_decision_and_a_shared_first_name_does_not(self):
        petrov = self.seed_user("petrov.t238@t238.example", "Петров Пётр", "agronomist", self.alpha)
        args = self.create_args("p.petrov.t238@t238.example", "Пётр Петров", "manager", self.beta, "petrov.env")
        blocked = self.tool.run_create(args, self.runtime)
        self.assertEqual(blocked.exit_code, self.tool.EXIT_CONFLICT)
        self.assertEqual(self.row("p.petrov.t238@t238.example"), [])
        decided = self.tool.run_create(argparse.Namespace(**{**vars(args), "distinct_from": [petrov]}), self.runtime)
        self.assertEqual(decided.exit_code, 0, decided.error)
        self.assertEqual(len(self.row("p.petrov.t238@t238.example")), 1)
        umid = self.tool.run_create(self.create_args("manager.t238@t238.example", "Байбутаев Умид Шавкатович",
                                                     "manager", self.beta, "manager.env"), self.runtime)
        self.assertEqual((umid.exit_code, umid.preflight["person_matches"]), (0, []))

    def test_enterprise_and_role_rules(self):
        before = self.snapshot()
        for enterprise in (self.closed, 99999):
            with self.subTest(enterprise=enterprise):
                outcome = self.tool.run_create(self.create_args("x.t238@t238.example", "Иксов Икс", "agronomist",
                                                                enterprise, f"x{enterprise}.env"), self.runtime)
                self.assertEqual(outcome.exit_code, self.tool.EXIT_ENTERPRISE)
        for role, code in (("admin", self.tool.EXIT_ROLE_REJECTED), ("superuser", self.tool.EXIT_INVALID_INPUT)):
            with self.subTest(role=role):
                status, _, stderr = self.main([
                    "create", "--login", "x.t238@t238.example", "--full-name", "Иксов Икс", "--role", role,
                    "--enterprise-id", str(self.alpha), "--expected-database", self.database_name,
                    "--credential-file", str(self.credentials / "x.env"), "--reason", "r",
                    "--audit-dir", str(self.audit_dir), "--apply",
                ])
                self.assertEqual(status, code, stderr)
        self.assertEqual(self.snapshot(), before)

    def test_database_identity_guard_uses_the_real_database_name(self):
        before = self.snapshot()
        with self.assertRaises(self.tool.AccountError) as caught:
            self.tool.run_create(self.create_args("x.t238@t238.example", "Иксов Икс", "agronomist", self.alpha,
                                                  "x.env", expected_database="agrosat"), self.runtime)
        self.assertEqual(caught.exception.code, self.tool.EXIT_DATABASE_IDENTITY)
        self.assertEqual(self.snapshot(), before)

    # ── failure handling ────────────────────────────────────────────────────

    def test_failed_create_commit_leaves_no_row_and_no_credential(self):
        with patch.object(self.tool, "_commit", side_effect=RuntimeError("synthetic commit failure")):
            outcome = self.tool.run_create(self.create_args("ivanov.t238@t238.example", "Иванов Иван", "agronomist",
                                                            self.beta, "ivanov.env"), self.runtime)
        self.assertEqual(outcome.exit_code, self.tool.EXIT_DATABASE_UPDATE)
        self.assertEqual(self.row("ivanov.t238@t238.example"), [])
        self.assertFalse((self.credentials / "ivanov.env").exists())
        self.assertEqual(outcome.result["outcome"], "NOT_CHANGED")
        self.assertTrue(outcome.result["credential_file_removed"])

    def test_create_verification_failure_removes_the_account(self):
        runtime = replace(self.runtime, pwd_context=_RefusingVerify(self.pwd_context))
        outcome = self.tool.run_create(self.create_args("ivanov.t238@t238.example", "Иванов Иван", "agronomist",
                                                        self.beta, "ivanov.env"), runtime)
        self.assertEqual(outcome.exit_code, self.tool.EXIT_VERIFICATION_ROLLED_BACK)
        self.assertEqual(self.row("ivanov.t238@t238.example"), [])
        self.assertFalse((self.credentials / "ivanov.env").exists())
        self.assertTrue(outcome.result["rollback_succeeded"])

    def test_reset_rotates_only_the_credential(self):
        created = self.tool.run_create(self.create_args("ivanov.t238@t238.example", "Иванов Иван", "agronomist",
                                                        self.beta, "ivanov.env"), self.runtime)
        self.assertEqual(created.exit_code, 0, created.error)
        _, old_secret = _credential(self.credentials / "ivanov.env")
        self.assertEqual(self.sign_in("ivanov.t238@t238.example", old_secret).status_code, 200)
        self.sql("UPDATE users SET phone = '+998900000238' WHERE email = 'ivanov.t238@t238.example'")
        before = self.row("ivanov.t238@t238.example")[0]
        user_id = before["id"]
        outcome = self.tool.run_reset(self.reset_args("IVANOV.t238@t238.example", user_id, "ivanov-reset.env"),
                                      self.runtime)
        self.assertEqual(outcome.exit_code, 0, outcome.error)
        after = self.row("ivanov.t238@t238.example")[0]
        self.assertEqual({k: v for k, v in after.items() if k != "hashed_password"},
                         {k: v for k, v in before.items() if k != "hashed_password"})
        self.assertNotEqual(after["hashed_password"], before["hashed_password"])
        username, new_secret = _credential(self.credentials / "ivanov-reset.env")
        self.assertEqual(username, "ivanov.t238@t238.example")
        self.assertNotEqual(new_secret, old_secret)
        self.assertEqual(self.sign_in("ivanov.t238@t238.example", old_secret).status_code, 401)
        self.assertEqual(self.sign_in("ivanov.t238@t238.example", new_secret).status_code, 200)
        self.assertNotIn(new_secret, json.dumps(outcome.result, ensure_ascii=False) + self.audit_text())

    def test_reset_refusals_change_nothing(self):
        petrov = self.seed_user("petrov.t238@t238.example", "Петров Пётр", "agronomist", self.alpha,
                                password=secrets.token_urlsafe(18))
        self.seed_user("twin.t238@t238.example", "Близнец Первый", "viewer", self.alpha)
        self.seed_user("TWIN.t238@t238.example", "Близнец Второй", "viewer", self.alpha)
        before = self.snapshot()
        cases = (
            ("admin", "admin@t238.example", self.admin, self.tool.EXIT_ROLE_REJECTED),
            ("missing", "nobody.t238@t238.example", petrov, self.tool.EXIT_TARGET_IDENTITY),
            ("wrong-id", "petrov.t238@t238.example", self.saidov, self.tool.EXIT_TARGET_IDENTITY),
            ("ambiguous", "twin.t238@t238.example", petrov, self.tool.EXIT_TARGET_IDENTITY),
        )
        for label, login, user_id, code in cases:
            with self.subTest(case=label):
                outcome = self.tool.run_reset(self.reset_args(login, user_id, f"{label}.env"), self.runtime)
                self.assertEqual(outcome.exit_code, code)
                self.assertFalse((self.credentials / f"{label}.env").exists())
        self.assertEqual(self.snapshot(), before)

    def test_failed_reset_keeps_the_original_credential(self):
        old_secret = secrets.token_urlsafe(18)
        petrov = self.seed_user("petrov.t238@t238.example", "Петров Пётр", "agronomist", self.alpha, password=old_secret)
        with patch.object(self.tool, "_commit", side_effect=RuntimeError("synthetic commit failure")):
            failed = self.tool.run_reset(self.reset_args("petrov.t238@t238.example", petrov, "a.env"), self.runtime)
        self.assertEqual(failed.exit_code, self.tool.EXIT_DATABASE_UPDATE)
        self.assertFalse((self.credentials / "a.env").exists())
        self.assertEqual(self.sign_in("petrov.t238@t238.example", old_secret).status_code, 200)
        runtime = replace(self.runtime, pwd_context=_RefusingVerify(self.pwd_context))
        restored = self.tool.run_reset(self.reset_args("petrov.t238@t238.example", petrov, "b.env"), runtime)
        self.assertEqual(restored.exit_code, self.tool.EXIT_VERIFICATION_ROLLED_BACK)
        self.assertTrue(restored.result["rollback_succeeded"])
        self.assertFalse((self.credentials / "b.env").exists())
        self.assertEqual(self.sign_in("petrov.t238@t238.example", old_secret).status_code, 200)

    # ── serialisation and API verification ─────────────────────────────────

    def test_concurrent_change_is_serialised_and_rechecked_under_the_lock(self):
        from sqlalchemy import text

        holder = self.engine.connect()
        self.addCleanup(holder.close)
        holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": self.tool.ACCOUNT_LOCK_KEY})
        holder.commit()
        result = {}
        args = self.create_args("ivanov.t238@t238.example", "Иванов Иван", "agronomist", self.beta, "ivanov.env")
        worker = threading.Thread(target=lambda: result.setdefault("outcome", self.tool.run_create(args, self.runtime)))
        worker.start()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            waiting = self.scalar("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted")
            if waiting:
                break
            time.sleep(0.05)
        self.assertTrue(waiting, "the CLI never waited for the account lock")
        self.assertTrue((self.credentials / "ivanov.env").exists())
        self.seed_user("ivanov.t238@t238.example", "Сидоров Сидор", "viewer", self.alpha)
        holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": self.tool.ACCOUNT_LOCK_KEY})
        holder.commit()
        worker.join(60)
        self.assertFalse(worker.is_alive())
        outcome = result["outcome"]
        self.assertEqual(outcome.exit_code, self.tool.EXIT_CONFLICT)
        self.assertIn("changed after the preflight", outcome.error)
        self.assertEqual([row["full_name"] for row in self.row("ivanov.t238@t238.example")], ["Сидоров Сидор"])
        self.assertFalse((self.credentials / "ivanov.env").exists())

    def test_api_verification_signs_in_through_the_application(self):
        client = self.client()

        def through_application(method, url, *, data=None, bearer=None, timeout=8.0):
            headers = {"Authorization": "Bearer " + bearer} if bearer else {}
            response = client.request(method, urlsplit(url).path, data=data, headers=headers)
            return response.status_code, response.json()

        with patch.object(self.tool.admin_tool, "_http_json", side_effect=through_application):
            outcome = self.tool.run_create(self.create_args(
                "yashkin.t238@t238.example", "Яшкин Василий", "agronomist", self.alpha, "yashkin.env",
                verify_api_base="http://127.0.0.1:8000"), self.runtime)
        self.assertEqual(outcome.exit_code, 0, outcome.error)
        self.assertTrue(outcome.result["api_login_verified"] and outcome.result["api_me_verified"])
        self.assertIsNotNone(self.row("yashkin.t238@t238.example")[0]["last_login"])


if __name__ == "__main__":
    unittest.main()
