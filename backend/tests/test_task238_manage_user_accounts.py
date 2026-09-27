"""TASK_238: the non-admin account CLI, without a database.

The orchestration runs against an in-memory store with real transaction
semantics (a working copy per store, applied only on commit). Credential
paths, files and access control lists are exercised on the real file system;
the PostgreSQL behaviour is covered by test_task238_user_accounts_postgres.py.
"""

from __future__ import annotations

import argparse
import ast
import copy
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from dotenv import dotenv_values

from scripts import manage_user_accounts as tool


HEAD = "0016_operational_command_center"
ALPHABET = set(tool._UPPER + tool._LOWER + tool._DIGITS + tool._SYMBOLS)


# ─── helpers ─────────────────────────────────────────────────────────────────


def current_user_sid() -> str:
    completed = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        capture_output=True, text=True, errors="replace", check=True,
    )
    return completed.stdout.strip().split(",")[-1].strip().strip('"')


def private_directory(parent: Path) -> Path:
    """A directory only SYSTEM, Administrators and the current user can open."""
    path = Path(tempfile.mkdtemp(prefix="cred-", dir=parent)).resolve()
    if os.name == "nt":
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r",
             "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F", f"*{current_user_sid()}:(OI)(CI)F"],
            capture_output=True, text=True, errors="replace", check=True,
        )
    else:
        os.chmod(path, 0o700)
    return path


def read_credential(path: Path) -> tuple[str, str]:
    values = {key: value for key, value in dotenv_values(path).items() if value is not None}
    usernames = [key for key in values if "USERNAME" in key.upper() or "EMAIL" in key.upper()]
    passwords = [key for key in values if "PASSWORD" in key.upper()]
    assert len(usernames) == 1 and len(passwords) == 1, sorted(values)
    return values[usernames[0]], values[passwords[0]]


class FakeContext:
    """Deterministic stand-in for the bcrypt context; the hash is still one-way."""

    def hash(self, password: str) -> str:
        return "fake$" + hashlib.sha256(password.encode("utf-8")).hexdigest()

    def verify(self, password: str, hashed: str) -> bool:
        return hashed == self.hash(password)


class FakeDatabase:
    def __init__(self, *, name: str = "agrosat_unit", revision: str | None = HEAD):
        self.name = name
        self.revision = revision
        self.enterprises = {
            1: tool.Enterprise(1, "Alpha", True),
            2: tool.Enterprise(2, "Beta", True),
            3: tool.Enterprise(3, "Closed", False),
        }
        self.users: dict[int, dict] = {}
        self.next_id = 100
        self.commits = 0
        self.opened = 0
        self.read_only_opened = 0
        self.locks = 0
        self.name_checks = 0
        self.fail_commit = False
        self.before_delete = None

    def add(self, email, *, role="agronomist", enterprise_id=1, full_name=None, password="Old-Credential-0",
            is_active=True, phone=None, user_id=None):
        user_id = user_id or self.next_id
        self.next_id = max(self.next_id, user_id) + 1
        self.users[user_id] = {
            "id": user_id, "email": email, "full_name": full_name, "phone": phone, "role": role,
            "enterprise_id": enterprise_id, "hashed_password": FakeContext().hash(password),
            "is_active": is_active, "created_at": datetime(2026, 6, 1, 8, 0, 0),
            "last_login": datetime(2026, 7, 1, 9, 30, 0),
        }
        return user_id


class FakeStore:
    def __init__(self, database: FakeDatabase):
        self.db = database
        self.view = copy.deepcopy(database.users)
        self.next_id = database.next_id
        self.read_only = False
        database.opened += 1

    def _write(self):
        if self.read_only:
            raise RuntimeError("write attempted in a READ ONLY transaction")

    def begin_read_only(self):
        self.read_only = True
        self.db.read_only_opened += 1

    def database_name(self):
        self.db.name_checks += 1
        return self.db.name

    def target(self):
        return tool.DatabaseTarget(self.db.name, "127.0.0.1", 5432, self.db.revision)

    def find_enterprise(self, enterprise_id):
        return self.db.enterprises.get(enterprise_id)

    @staticmethod
    def _account(row):
        return tool.Account(row["id"], row["email"], row["full_name"], tool.normalize_role(row["role"]),
                            row["enterprise_id"], bool(row["is_active"]), row["created_at"], row["last_login"])

    def accounts_by_login(self, login, *, lock=False):
        rows = [row for _, row in sorted(self.view.items()) if row["email"].strip().lower() == login]
        return [self._account(row) for row in rows[:3]]

    def accounts(self):
        return [self._account(row) for _, row in sorted(self.view.items())]

    def lock_account_changes(self):
        self._write()
        self.db.locks += 1

    def insert_account(self, *, login, full_name, role, enterprise_id, password_hash):
        self._write()
        if any(row["email"] == login for row in self.view.values()):
            raise RuntimeError("duplicate key value violates unique constraint users_email_key")
        user_id = self.next_id
        self.next_id += 1
        self.view[user_id] = {
            "id": user_id, "email": login, "full_name": full_name, "phone": None, "role": role,
            "enterprise_id": enterprise_id, "hashed_password": password_hash, "is_active": True,
            "created_at": datetime(2026, 9, 27, 10, 0, 0), "last_login": None,
        }
        return user_id

    def credential_state(self, user_id, *, lock=False):
        row = self.view.get(user_id)
        if row is None:
            return None
        profile = {name: row[name] for name in tool.PRESERVED_ON_RESET}
        profile["role"] = tool.normalize_role(profile["role"])
        return profile, row["hashed_password"]

    def replace_password_hash(self, user_id, expected_hash, new_hash):
        self._write()
        row = self.view.get(user_id)
        if row is None or row["hashed_password"] != expected_hash:
            return False
        row["hashed_password"] = new_hash
        return True

    def delete_created_account(self, user_id, login, expected_hash):
        self._write()
        if self.db.before_delete:
            self.db.before_delete(self)
        row = self.view.get(user_id)
        if row is None or row["email"] != login or row["hashed_password"] != expected_hash:
            return False
        del self.view[user_id]
        return True

    def commit(self):
        if self.db.fail_commit:
            raise RuntimeError("synthetic commit failure with secret-looking text Pa55word!")
        self.db.users = self.view
        self.db.next_id = max(self.db.next_id, self.next_id)
        self.db.commits += 1

    def rollback(self):
        self.view = copy.deepcopy(self.db.users)

    def close(self):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="t238-unit-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.credentials = self.root / "credentials"
        self.credentials.mkdir()
        self.audit_dir = self.root / "audit"
        self.db = FakeDatabase()
        self.context = FakeContext()
        self.expected_head = HEAD
        self.runtime = tool.Runtime(
            open_store=lambda: FakeStore(self.db),
            pwd_context=self.context,
            environment="test",
            expected_head=lambda: self.expected_head,
        )
        # Orchestration tests use a plain directory; access control has its own tests.
        privacy = patch.object(tool, "_privacy_problem", return_value=None)
        privacy.start()
        self.addCleanup(privacy.stop)

    def create_args(self, **overrides):
        values = {
            "command": "create", "login": "ivanov@agrosat.uz", "full_name": "Иванов Иван",
            "role": "agronomist", "enterprise_id": 1, "distinct_from": [],
            "expected_database": "agrosat_unit", "credential_file": str(self.credentials / "ivanov.env"),
            "reason": "TASK_238 unit test", "audit_dir": str(self.audit_dir), "verify_api_base": None,
            "apply": True,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def reset_args(self, user_id, **overrides):
        values = {
            "command": "reset-password", "login": "petrov@agrosat.uz", "expected_user_id": user_id,
            "expected_database": "agrosat_unit", "credential_file": str(self.credentials / "petrov-reset.env"),
            "reason": "TASK_238 unit test", "audit_dir": str(self.audit_dir), "verify_api_base": None,
            "apply": True,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def audit_documents(self):
        return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(self.audit_dir.glob("*.json"))]

    def run_main(self, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(tool, "_load_runtime", return_value=self.runtime), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = tool.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def argv_create(self, *extra, login="ivanov@agrosat.uz", name="Иванов Иван", role="agronomist",
                    enterprise="1", credential="ivanov.env"):
        return ["create", "--login", login, "--full-name", name, "--role", role, "--enterprise-id", enterprise,
                "--expected-database", "agrosat_unit", "--credential-file", str(self.credentials / credential),
                "--reason", "TASK_238 unit test", "--audit-dir", str(self.audit_dir), *extra]


# ─── parsing, identities and generation ──────────────────────────────────────


class ParserTests(unittest.TestCase):
    def options(self, command):
        parser = tool.build_parser()
        choices = parser._subparsers._group_actions[0].choices
        return {option for action in choices[command]._actions for option in action.option_strings}

    def test_no_password_option_and_mutation_is_opt_in(self):
        for command in ("create", "reset-password", "inspect"):
            with self.subTest(command=command):
                options = self.options(command)
                self.assertFalse(any("password" in option for option in options), options)
        args = tool.build_parser().parse_args([
            "create", "--login", "a@b.cd", "--full-name", "A B", "--role", "viewer", "--enterprise-id", "1",
            "--expected-database", "x", "--credential-file", "C:/x.env", "--reason", "r",
        ])
        self.assertFalse(args.apply)

    def test_abbreviated_flags_are_refused(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            tool.build_parser().parse_args([
                "create", "--login", "a@b.cd", "--full-name", "A B", "--role", "viewer", "--enterprise-id", "1",
                "--expected-database", "x", "--credential-file", "C:/x.env", "--reason", "r", "--app",
            ])

    def test_managed_roles_are_exactly_the_tenant_roles(self):
        from api.dependencies import TENANT_ROLES
        from models.monitoring import UserRole

        self.assertEqual(set(tool.MANAGED_ROLES), set(TENANT_ROLES))
        self.assertEqual(set(tool.MANAGED_ROLES) | {tool.ADMIN_ROLE}, {role.value for role in UserRole})


class IdentityTests(unittest.TestCase):
    def test_login_is_normalized_like_the_login_endpoint(self):
        self.assertEqual(tool.validate_new_login("  Ivanov@AgroSat.UZ "), "ivanov@agrosat.uz")
        self.assertEqual(tool.normalize_login(" DEMO.Agronomist.task202@agrosat.local "),
                         "demo.agronomist.task202@agrosat.local")

    def test_malformed_new_logins_are_rejected(self):
        for login in ("ivanov", "iva nov@agrosat.uz", "иванов@agrosat.uz", "a..b@agrosat.uz", "-a@agrosat.uz",
                      "a@agrosat", "a@-agrosat.uz", "a@agrosat.uz.", "", "   ", "a@b@c.uz"):
            with self.subTest(login=login):
                with self.assertRaises(tool.AccountError) as caught:
                    tool.validate_new_login(login)
                self.assertEqual(caught.exception.code, tool.EXIT_INVALID_INPUT)

    def test_full_name_is_cleaned_and_bounded(self):
        self.assertEqual(tool.clean_full_name("  Яшкин   Василий "), "Яшкин Василий")
        for value in ("", "   ", "Яшкин\x00Василий", "Я" * 256):
            with self.subTest(length=len(value)):
                with self.assertRaises(tool.AccountError):
                    tool.clean_full_name(value)

    def test_same_person_needs_two_name_parts(self):
        same = tool.same_person
        tokens = tool.name_tokens
        self.assertTrue(same(tokens("Иванов Иван"), tokens("иван  ИВАНОВ")))
        self.assertTrue(same(tokens("Пётр Петров"), tokens("Петров Петр")))
        self.assertTrue(same(tokens("Иванов Иван"), tokens("Иванов Иван Петрович")))
        self.assertTrue(same(tokens("Байбутаев Умид Шавкатович"), tokens("Байбутаев Умид")))
        self.assertFalse(same(tokens("Байбутаев Умид Шавкатович"), tokens("Умид Саидов")))
        self.assertFalse(same(tokens("Иван"), tokens("Иванов Иван")))
        self.assertFalse(same(tokens("Иванов Иван"), tokens("Иванова Ивана")))
        self.assertFalse(same(tokens("Иванов Иван"), ()))


class ApiBaseTests(unittest.TestCase):
    def test_credentials_are_only_sent_over_loopback_or_tls(self):
        for base in ("http://127.0.0.1:8000", "http://localhost:8000/", "https://agrosat.example"):
            with self.subTest(base=base):
                self.assertTrue(tool._api_base(base).startswith(base.rstrip("/")))
        for base in ("http://agrosat.example", "http://10.0.0.5:8000", "https://user:pw@agrosat.example",
                     "ftp://127.0.0.1", "http://127.0.0.1:8000/?x=1"):
            with self.subTest(base=base):
                with self.assertRaises(tool.AccountError) as caught:
                    tool._api_base(base)
                self.assertEqual(caught.exception.code, tool.EXIT_INVALID_INPUT)


class GenerationTests(unittest.TestCase):
    def test_generated_credentials_are_strong_distinct_and_dotenv_safe(self):
        from scripts import manage_admin_credentials as admin_tool

        samples = [tool.generate_password() for _ in range(400)]
        self.assertEqual(len(set(samples)), len(samples))
        for value in samples:
            self.assertEqual(len(value), tool.CREDENTIAL_LENGTH)
            self.assertTrue(set(value) <= ALPHABET)
            self.assertTrue(any(c in tool._UPPER for c in value))
            self.assertTrue(any(c in tool._LOWER for c in value))
            self.assertTrue(any(c in tool._DIGITS for c in value))
            self.assertTrue(any(c in tool._SYMBOLS for c in value))
            self.assertEqual(admin_tool.validate_password(value), value)
            self.assertFalse(set(value) & set("\"'#$=\\ \t"))
        self.assertLessEqual(len(samples[0].encode("utf-8")), 72)  # bcrypt input limit

    def test_generation_uses_the_secrets_module(self):
        with patch.object(tool.secrets, "choice", wraps=tool.secrets.choice) as choice:
            tool.generate_password()
        self.assertGreaterEqual(choice.call_count, tool.CREDENTIAL_LENGTH)


# ─── credential location and file ────────────────────────────────────────────


class CredentialPathTests(Base):
    def assert_rejected(self, value, audit_dir=None):
        with self.assertRaises(tool.AccountError) as caught:
            tool.check_credential_path(value, audit_dir=audit_dir)
        self.assertEqual(caught.exception.code, tool.EXIT_CREDENTIAL_OUTPUT)
        return str(caught.exception)

    def test_relative_traversal_and_unsafe_names_are_rejected(self):
        self.assert_rejected("relative.env")
        self.assert_rejected(str(self.credentials / ".." / "credentials" / "x.env"))
        for name in ("x.env:stream", "NUL.env", "con", "x.env.", ".hidden", "sp ace.env", "ы.env"):
            with self.subTest(name=name):
                self.assert_rejected(str(self.credentials / name))

    def test_missing_directory_and_existing_file_are_rejected(self):
        self.assert_rejected(str(self.root / "missing" / "x.env"))
        existing = self.credentials / "existing.env"
        existing.write_text("keep", encoding="utf-8")
        self.assertIn("never overwritten", self.assert_rejected(str(existing)))
        self.assertEqual(existing.read_text(encoding="utf-8"), "keep")

    def test_repository_release_and_audit_locations_are_rejected(self):
        self.assert_rejected(str(tool.BACKEND / "scripts" / "x.env"))
        repo = self.root / "repo"
        (repo / "nested").mkdir(parents=True)
        (repo / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
        self.assertIn("Git", self.assert_rejected(str(repo / "nested" / "x.env")))
        release = self.root / "release"
        (release / "backend").mkdir(parents=True)
        (release / "release-manifest.json").write_text("{}", encoding="utf-8")
        self.assertIn("release", self.assert_rejected(str(release / "backend" / "x.env")))
        self.audit_dir.mkdir()
        self.assertIn("audit", self.assert_rejected(str(self.audit_dir / "x.env"), audit_dir=self.audit_dir))

    def test_links_and_junctions_are_rejected(self):
        link = self.root / "link"
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(self.credentials)],
                           capture_output=True, text=True, errors="replace", check=True)
        else:
            os.symlink(self.credentials, link)
        self.assertIn("real path", self.assert_rejected(str(link / "x.env")))

    def test_accepted_path_is_returned_resolved_and_not_created(self):
        target = tool.check_credential_path(str(self.credentials / "ok.env"))
        self.assertEqual(target, self.credentials / "ok.env")
        self.assertFalse(target.exists())


class AccessControlTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="t238-acl-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_acl_rules(self):
        me = "S-1-5-21-1-2-3-1001"
        base = {"protected": True, "user": me, "rules": [
            {"sid": "S-1-5-18", "allow": True, "inherit_only": False},
            {"sid": "S-1-5-32-544", "allow": True, "inherit_only": False},
            {"sid": me, "allow": True, "inherit_only": False},
            {"sid": "S-1-3-0", "allow": True, "inherit_only": True},
            {"sid": "S-1-1-0", "allow": False, "inherit_only": False},
        ]}
        self.assertIsNone(tool._acl_problem(base, directory=True))
        self.assertIn("inherits", tool._acl_problem(dict(base, protected=False), directory=True))
        self.assertIsNone(tool._acl_problem(dict(base, protected=False), directory=False))
        for sid in ("S-1-5-32-545", "S-1-1-0", "S-1-5-11", "S-1-5-4", "S-1-5-21-1-2-3-1002"):
            with self.subTest(sid=sid):
                widened = dict(base, rules=base["rules"] + [{"sid": sid, "allow": True, "inherit_only": False}])
                self.assertIn(sid, tool._acl_problem(widened, directory=True))
        owner_rights = dict(base, rules=base["rules"] + [{"sid": "S-1-3-4", "allow": True, "inherit_only": False}])
        self.assertIsNone(tool._acl_problem(owner_rights, directory=True))
        creator = dict(base, rules=[{"sid": "S-1-3-0", "allow": True, "inherit_only": False}])
        self.assertIsNotNone(tool._acl_problem(creator, directory=True))

    def test_real_private_and_shared_directories(self):
        private = private_directory(self.root)
        self.assertIsNone(tool._privacy_problem(private, directory=True))
        shared = self.root / "shared"
        shared.mkdir()
        if os.name != "nt":
            os.chmod(shared, 0o755)
        self.assertIsNotNone(tool._privacy_problem(shared, directory=True))
        with self.assertRaises(tool.AccountError):
            tool.check_credential_path(str(shared / "x.env"))
        if os.name == "nt":
            widened = private_directory(self.root)
            subprocess.run(["icacls", str(widened), "/grant", "*S-1-5-32-545:(OI)(CI)R"],
                           capture_output=True, text=True, errors="replace", check=True)
            self.assertIn("S-1-5-32-545", tool._privacy_problem(widened, directory=True))

    def test_credential_file_is_exclusive_private_and_matches_the_handoff_contract(self):
        private = private_directory(self.root)
        target = tool.check_credential_path(str(private / "hand-off.env"))
        secret = tool.generate_password()
        handle = tool.write_credential_file(target, tool._credential_body(
            "ivanov@agrosat.uz", secret, operation="create", role="agronomist", enterprise_id=9, user_id=None))
        self.assertTrue(handle.written)
        self.assertEqual(read_credential(target), ("ivanov@agrosat.uz", secret))
        self.assertIsNone(tool._privacy_problem(target, directory=False))
        if os.name != "nt":
            self.assertEqual(os.stat(target).st_mode & 0o777, 0o600)
        with self.assertRaises(tool.AccountError) as caught:
            tool.write_credential_file(target, "replacement")
        self.assertEqual(caught.exception.code, tool.EXIT_CREDENTIAL_OUTPUT)
        self.assertEqual(read_credential(target), ("ivanov@agrosat.uz", secret))
        self.assertTrue(handle.remove())
        self.assertFalse(target.exists())

    def test_file_that_is_not_private_is_removed_before_any_secret_is_written(self):
        target = self.root / "leaky.env"
        with patch.object(tool, "_privacy_problem", return_value="it grants access to S-1-1-0"):
            with self.assertRaises(tool.AccountError):
                tool.write_credential_file(target, "AGROSAT_LOGIN_PASSWORD=never-written")
        self.assertFalse(target.exists())


# ─── create ──────────────────────────────────────────────────────────────────


class CreateTests(Base):
    def test_dry_run_reports_the_plan_and_writes_nothing(self):
        args = self.create_args(apply=False, audit_dir=None)
        outcome = tool.run_create(args, self.runtime)
        self.assertEqual(outcome.exit_code, 0)
        self.assertEqual(outcome.preflight["plan"], "CREATE")
        self.assertEqual(outcome.preflight["mode"], "dry-run")
        self.assertTrue(outcome.preflight["credential_file"]["usable"])
        self.assertEqual(outcome.preflight["database_target"]["database_name"], "agrosat_unit")
        self.assertTrue(outcome.preflight["database_target"]["revision_match"])
        self.assertIsNone(outcome.result)
        self.assertEqual((self.db.commits, self.db.users, self.db.locks), (0, {}, 0))
        self.assertEqual(self.db.opened, self.db.read_only_opened)
        self.assertFalse((self.credentials / "ivanov.env").exists())
        self.assertFalse(self.audit_dir.exists())

    def test_manager_and_agronomist_are_created_bound_active_and_verifiable(self):
        cases = (("baybutayev@agrosat.uz", "Байбутаев Умид Шавкатович", "manager", "2", "manager.env"),
                 ("yashkin@agrosat.uz", "Яшкин Василий", "agronomist", "1", "yashkin.env"))
        for login, name, role, enterprise, file_name in cases:
            with self.subTest(role=role):
                code, stdout, stderr = self.run_main(self.argv_create(
                    "--apply", login=login, name=name, role=role, enterprise=enterprise, credential=file_name))
                self.assertEqual(code, 0, stderr)
                created = [row for row in self.db.users.values() if row["email"] == login]
                self.assertEqual(len(created), 1)
                row = created[0]
                self.assertEqual((row["role"], row["enterprise_id"], row["is_active"], row["full_name"]),
                                 (role, int(enterprise), True, name))
                self.assertIsNone(row["phone"])
                username, secret = read_credential(self.credentials / file_name)
                self.assertEqual(username, login)
                self.assertTrue(self.context.verify(secret, row["hashed_password"]))
                lines = [json.loads(line) for line in stdout.splitlines()]
                self.assertEqual([line["stage"] for line in lines], ["preflight", "result"])
                self.assertEqual(lines[1]["outcome"], "CREATED")
                self.assertTrue(lines[1]["database_changed"])
                self.assertTrue(lines[1]["credential_file_written"])
                combined = stdout + stderr + "".join(p.read_text(encoding="utf-8") for p in self.audit_dir.iterdir())
                self.assertNotIn(secret, combined)
                self.assertNotIn(row["hashed_password"], combined)
        self.assertEqual(self.db.locks, 2)

    def test_rerun_is_idempotent_and_writes_no_new_credential(self):
        self.assertEqual(tool.run_create(self.create_args(), self.runtime).exit_code, 0)
        first = copy.deepcopy(self.db.users)
        for credential in ("ivanov.env", "ivanov-again.env"):
            with self.subTest(credential=credential):
                outcome = tool.run_create(
                    self.create_args(credential_file=str(self.credentials / credential)), self.runtime)
                self.assertEqual(outcome.exit_code, 0)
                self.assertEqual(outcome.preflight["plan"], "ALREADY_EXISTS")
                self.assertEqual(outcome.result["outcome"], "NO_CHANGE_ALREADY_EXISTS")
                self.assertFalse(outcome.result["database_changed"])
        self.assertEqual(self.db.users, first)
        self.assertFalse((self.credentials / "ivanov-again.env").exists())

    def test_login_collisions_are_refused(self):
        cases = (
            ("exact", lambda: self.db.add("ivanov@agrosat.uz", full_name="Сидоров Сидор")),
            ("case", lambda: self.db.add("Ivanov@AgroSat.uz", full_name="Иванов Иван")),
            ("inactive", lambda: self.db.add("ivanov@agrosat.uz", full_name="Иванов Иван", is_active=False)),
            ("role", lambda: self.db.add("ivanov@agrosat.uz", full_name="Иванов Иван", role="viewer")),
            ("several", lambda: (self.db.add("IVANOV@agrosat.uz"), self.db.add("ivanov@AGROSAT.uz"))),
        )
        for label, seed in cases:
            with self.subTest(case=label):
                self.db = FakeDatabase()
                seed()
                before = copy.deepcopy(self.db.users)
                outcome = tool.run_create(self.create_args(credential_file=str(self.credentials / f"{label}.env")),
                                          self.runtime)
                self.assertEqual(outcome.exit_code, tool.EXIT_CONFLICT)
                self.assertEqual(self.db.users, before)
                self.assertFalse((self.credentials / f"{label}.env").exists())

    def test_same_person_conflict_needs_an_explicit_decision(self):
        existing = self.db.add("petrov@agrosat.uz", full_name="Петров Пётр", role="agronomist", enterprise_id=1)
        args = self.create_args(login="p.petrov@agrosat.uz", full_name="Пётр Петров", role="manager",
                                enterprise_id=2, credential_file=str(self.credentials / "petrov.env"))
        outcome = tool.run_create(args, self.runtime)
        self.assertEqual(outcome.exit_code, tool.EXIT_CONFLICT)
        self.assertIn("Operator decision required", outcome.error)
        self.assertEqual([m["id"] for m in outcome.preflight["person_matches"]], [existing])
        self.assertEqual(len(self.db.users), 1)
        wrong = tool.run_create(argparse.Namespace(**{**vars(args), "distinct_from": [999]}), self.runtime)
        self.assertEqual(wrong.exit_code, tool.EXIT_INVALID_INPUT)
        decided = tool.run_create(argparse.Namespace(**{**vars(args), "distinct_from": [existing]}), self.runtime)
        self.assertEqual(decided.exit_code, 0)
        self.assertEqual(len(self.db.users), 2)
        passed = [audit for audit in self.audit_documents() if audit["result"] == "PASS"]
        self.assertEqual([audit["distinct_from_user_ids"] for audit in passed], [[existing]])

    def test_shared_first_name_is_not_a_duplicate(self):
        self.db.add("agronom@agrosat.uz", full_name="Умид Саидов", role="viewer", enterprise_id=2)
        outcome = tool.run_create(self.create_args(login="baybutayev@agrosat.uz",
                                                   full_name="Байбутаев Умид Шавкатович", role="manager",
                                                   enterprise_id=2), self.runtime)
        self.assertEqual(outcome.exit_code, 0)
        self.assertEqual(outcome.preflight["person_matches"], [])

    def test_enterprise_role_and_schema_are_validated(self):
        for enterprise_id in (3, 42):
            with self.subTest(enterprise=enterprise_id):
                outcome = tool.run_create(self.create_args(enterprise_id=enterprise_id), self.runtime)
                self.assertEqual(outcome.exit_code, tool.EXIT_ENTERPRISE)
        with self.assertRaises(tool.AccountError) as caught:
            tool.run_create(self.create_args(role="admin"), self.runtime)
        self.assertEqual(caught.exception.code, tool.EXIT_ROLE_REJECTED)
        for role in ("superuser", "", "Leadership"):
            with self.subTest(role=role):
                with self.assertRaises(tool.AccountError) as caught:
                    tool.run_create(self.create_args(role=role), self.runtime)
                self.assertEqual(caught.exception.code, tool.EXIT_INVALID_INPUT)
        self.assertEqual(tool.run_create(self.create_args(role=" Manager "), self.runtime).exit_code, 0)
        self.db = FakeDatabase(revision="0015_closed_loop_agronomy")
        self.assertEqual(tool.run_create(self.create_args(login="x@agrosat.uz", credential_file=str(
            self.credentials / "x.env")), self.runtime).exit_code, tool.EXIT_SCHEMA_MISMATCH)
        self.expected_head = None
        self.assertEqual(tool.run_create(self.create_args(login="y@agrosat.uz", credential_file=str(
            self.credentials / "y.env")), self.runtime).exit_code, tool.EXIT_SCHEMA_MISMATCH)
        self.assertEqual(len(self.db.users), 0)

    def test_database_identity_is_checked_before_any_read(self):
        self.db.name = "agrosat"
        outcome_error = None
        try:
            tool.run_create(self.create_args(), self.runtime)
        except tool.AccountError as exc:
            outcome_error = exc
        self.assertEqual(outcome_error.code, tool.EXIT_DATABASE_IDENTITY)
        self.assertEqual((self.db.commits, self.db.name_checks), (0, 1))

    def test_apply_needs_an_audit_directory_before_touching_the_database(self):
        opened = []
        runtime = tool.Runtime(lambda: opened.append(1), self.context, "test", lambda: HEAD)
        with self.assertRaises(tool.AccountError) as caught:
            tool.run_create(self.create_args(audit_dir=None), runtime)
        self.assertEqual(caught.exception.code, tool.EXIT_AUDIT_WRITE)
        self.assertEqual(opened, [])

    def test_failed_commit_leaves_no_account_and_no_credential(self):
        self.db.fail_commit = True
        code, stdout, stderr = self.run_main(self.argv_create("--apply"))
        self.assertEqual(code, tool.EXIT_DATABASE_UPDATE)
        self.assertEqual(self.db.users, {})
        self.assertFalse((self.credentials / "ivanov.env").exists())
        audit = self.audit_documents()[0]
        self.assertEqual((audit["result"], audit["outcome"]), ("BLOCKED", "NOT_CHANGED"))
        self.assertTrue(audit["credential_file_written"])
        self.assertTrue(audit["credential_file_removed"])
        self.assertNotIn("Pa55word", stdout + stderr + json.dumps(audit, ensure_ascii=False))

    def test_verification_failure_removes_the_new_account(self):
        with patch.object(self.context, "verify", return_value=False):
            outcome = tool.run_create(self.create_args(), self.runtime)
        self.assertEqual(outcome.exit_code, tool.EXIT_VERIFICATION_ROLLED_BACK)
        self.assertEqual(self.db.users, {})
        self.assertFalse((self.credentials / "ivanov.env").exists())
        audit = self.audit_documents()[0]
        self.assertEqual(audit["outcome"], "ROLLED_BACK")
        self.assertTrue(audit["rollback_attempted"] and audit["rollback_succeeded"])
        self.assertFalse(audit["user_row_created"])

    def test_unsafe_removal_is_critical_and_keeps_the_live_credential(self):
        def concurrent_change(store):
            for row in store.view.values():
                row["hashed_password"] = "changed-by-someone-else"

        self.db.before_delete = concurrent_change
        with patch.object(self.context, "verify", return_value=False):
            outcome = tool.run_create(self.create_args(), self.runtime)
        self.assertEqual(outcome.exit_code, tool.EXIT_CRITICAL_ROLLBACK)
        self.assertEqual(len(self.db.users), 1)
        self.assertTrue((self.credentials / "ivanov.env").exists())
        audit = self.audit_documents()[0]
        self.assertEqual(audit["outcome"], "CRITICAL_MANUAL_INTERVENTION")
        self.assertFalse(audit["credential_file_removed"])

    def test_conflict_appearing_after_preflight_is_caught_under_the_lock(self):
        real_read = tool._read_create_state

        def stale_preflight(runtime, expected, request):
            state = real_read(runtime, expected, request)
            self.db.add("ivanov@agrosat.uz", full_name="Сидоров Сидор")
            return state

        with patch.object(tool, "_read_create_state", side_effect=stale_preflight):
            outcome = tool.run_create(self.create_args(), self.runtime)
        self.assertEqual(outcome.exit_code, tool.EXIT_CONFLICT)
        self.assertEqual([row["full_name"] for row in self.db.users.values()], ["Сидоров Сидор"])
        self.assertFalse((self.credentials / "ivanov.env").exists())
        self.assertIn("changed after the preflight", outcome.error)

    def test_api_verification(self):
        calls = []

        def api(method, url, **kwargs):
            calls.append((method, url.rsplit("/", 1)[-1]))
            if url.endswith("/health"):
                return 200, {}
            if url.endswith("/login"):
                row = next(iter(self.db.users.values()))
                ok = self.context.verify(kwargs["data"]["password"], row["hashed_password"])
                return (200, {"access_token": "in-memory"}) if ok else (401, {})
            row = next(iter(self.db.users.values()))
            return 200, {key: row[key] for key in ("id", "email", "role", "enterprise_id", "is_active")}

        with patch.object(tool.admin_tool, "_http_json", side_effect=api):
            outcome = tool.run_create(self.create_args(verify_api_base="http://127.0.0.1:8000"), self.runtime)
        self.assertEqual(outcome.exit_code, 0, outcome.error)
        self.assertEqual([name for _, name in calls], ["health", "login", "me"])
        audit = self.audit_documents()[0]
        self.assertTrue(audit["api_health_verified"] and audit["api_login_verified"] and audit["api_me_verified"])
        self.assertNotIn("in-memory", json.dumps(audit))

    def test_api_failures(self):
        with patch.object(tool.admin_tool, "_http_json", return_value=(503, {})):
            outcome = tool.run_create(self.create_args(verify_api_base="http://127.0.0.1:8000"), self.runtime)
        self.assertEqual(outcome.exit_code, tool.EXIT_API_PREFLIGHT)
        self.assertEqual(self.db.users, {})
        self.assertFalse((self.credentials / "ivanov.env").exists())

        def refused_login(method, url, **kwargs):
            return (200, {}) if url.endswith("/health") else (401, {})

        with patch.object(tool.admin_tool, "_http_json", side_effect=refused_login):
            outcome = tool.run_create(self.create_args(verify_api_base="http://127.0.0.1:8000"), self.runtime)
        self.assertEqual(outcome.exit_code, tool.EXIT_VERIFICATION_ROLLED_BACK)
        self.assertEqual(self.db.users, {})

    def test_unexpected_error_text_is_withheld(self):
        def broken_store():
            raise RuntimeError("postgresql://operator:Hunter2-Secret@db/agrosat")

        runtime = tool.Runtime(broken_store, self.context, "test", lambda: HEAD)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(tool, "_load_runtime", return_value=runtime), \
                patch.object(tool, "_read_create_state", side_effect=RuntimeError("Hunter2-Secret")), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = tool.main(self.argv_create())
        self.assertEqual(code, tool.EXIT_UNEXPECTED)
        self.assertNotIn("Hunter2", stdout.getvalue() + stderr.getvalue())

    def test_unexpected_failure_before_the_change_is_audited_without_its_text(self):
        with patch.object(self.context, "hash", side_effect=ValueError("boom Hunter2-Secret")):
            outcome = tool.run_create(self.create_args(), self.runtime)
        self.assertEqual(outcome.exit_code, tool.EXIT_UNEXPECTED)
        self.assertEqual(self.db.users, {})
        self.assertFalse((self.credentials / "ivanov.env").exists())
        audit = self.audit_documents()[0]
        self.assertEqual((audit["outcome"], audit["error_code"]), ("NOT_CHANGED", tool.EXIT_UNEXPECTED))
        self.assertNotIn("Hunter2", json.dumps(audit) + json.dumps(outcome.result) + outcome.error)

    def test_audit_write_failure_still_reports_the_completed_change(self):
        with patch.object(tool, "write_audit", side_effect=tool.AccountError(tool.EXIT_AUDIT_WRITE, "audit down")):
            outcome = tool.run_create(self.create_args(), self.runtime)
        self.assertEqual(outcome.exit_code, tool.EXIT_AUDIT_WRITE)
        self.assertEqual((outcome.result["result"], outcome.result["outcome"]), ("PASS", "CREATED"))
        self.assertTrue(outcome.result["audit_write_failed"])
        self.assertEqual(len(self.db.users), 1)
        self.assertTrue((self.credentials / "ivanov.env").exists())

    def test_audit_has_only_the_documented_fields(self):
        tool.run_create(self.create_args(), self.runtime)
        audit = self.audit_documents()[0]
        self.assertEqual(set(audit), set(tool.AUDIT_FIELDS))
        self.assertEqual((audit["operation"], audit["result"], audit["outcome"]), ("create", "PASS", "CREATED"))
        self.assertEqual((audit["target_role"], audit["target_enterprise_id"]), ("agronomist", 1))
        self.assertTrue(audit["operator_os_user"] and audit["hostname"] and audit["timestamp_utc"])
        self.assertEqual(audit["database_name"], "agrosat_unit")
        text_files = list(self.audit_dir.glob("*.txt"))
        self.assertEqual(len(text_files), 1)


# ─── reset-password ──────────────────────────────────────────────────────────


class ResetTests(Base):
    OLD = "Old-Credential-0"

    def seed(self, **overrides):
        values = {"full_name": "Петров Пётр", "role": "agronomist", "enterprise_id": 2,
                  "phone": "+998900000000", "password": self.OLD}
        values.update(overrides)
        return self.db.add("petrov@agrosat.uz", **values)

    def test_dry_run_writes_nothing(self):
        user_id = self.seed()
        before = copy.deepcopy(self.db.users)
        outcome = tool.run_reset(self.reset_args(user_id, apply=False, audit_dir=None), self.runtime)
        self.assertEqual((outcome.exit_code, outcome.preflight["plan"]), (0, "RESET"))
        self.assertEqual((self.db.users, self.db.commits, self.db.locks), (before, 0, 0))
        self.assertFalse((self.credentials / "petrov-reset.env").exists())

    def test_reset_replaces_only_the_credential(self):
        user_id = self.seed()
        before = copy.deepcopy(self.db.users[user_id])
        outcome = tool.run_reset(self.reset_args(user_id), self.runtime)
        self.assertEqual(outcome.exit_code, 0, outcome.error)
        after = self.db.users[user_id]
        for name in ("email", "full_name", "phone", "role", "enterprise_id", "is_active", "created_at", "last_login"):
            self.assertEqual(after[name], before[name], name)
        self.assertNotEqual(after["hashed_password"], before["hashed_password"])
        username, secret = read_credential(self.credentials / "petrov-reset.env")
        self.assertEqual(username, "petrov@agrosat.uz")
        self.assertTrue(self.context.verify(secret, after["hashed_password"]))
        self.assertFalse(self.context.verify(self.OLD, after["hashed_password"]))
        audit = self.audit_documents()[0]
        self.assertEqual((audit["outcome"], audit["target_role"], audit["target_enterprise_id"]),
                         ("PASSWORD_RESET", "agronomist", 2))
        self.assertNotIn(secret, json.dumps(audit, ensure_ascii=False))

    def test_inactive_account_keeps_its_state(self):
        user_id = self.seed(is_active=False)
        self.assertEqual(tool.run_reset(self.reset_args(user_id), self.runtime).exit_code, 0)
        self.assertFalse(self.db.users[user_id]["is_active"])
        blocked = tool.run_reset(self.reset_args(user_id, verify_api_base="http://127.0.0.1:8000",
                                                 credential_file=str(self.credentials / "second.env")),
                                 self.runtime)
        self.assertEqual(blocked.exit_code, tool.EXIT_INVALID_INPUT)

    def test_rejected_targets_change_nothing(self):
        user_id = self.seed()
        admin = self.db.add("admin@agrosat.uz", role="admin", enterprise_id=None)
        odd = self.db.add("odd@agrosat.uz", role="superuser")
        mixed = self.db.add("Mixed@agrosat.uz", role="viewer")
        self.db.add("twin@agrosat.uz")
        self.db.add("TWIN@agrosat.uz")
        before = copy.deepcopy(self.db.users)
        cases = (
            ("admin", {"login": "admin@agrosat.uz", "expected_user_id": admin}, tool.EXIT_ROLE_REJECTED),
            ("missing", {"login": "nobody@agrosat.uz"}, tool.EXIT_TARGET_IDENTITY),
            ("wrong id", {"expected_user_id": user_id + 1000}, tool.EXIT_TARGET_IDENTITY),
            ("ambiguous", {"login": "twin@agrosat.uz"}, tool.EXIT_TARGET_IDENTITY),
            ("unsupported role", {"login": "odd@agrosat.uz", "expected_user_id": odd}, tool.EXIT_TARGET_IDENTITY),
            ("not normalized", {"login": "mixed@agrosat.uz", "expected_user_id": mixed}, tool.EXIT_TARGET_IDENTITY),
        )
        for label, overrides, code in cases:
            with self.subTest(case=label):
                credential = self.credentials / (label.replace(" ", "-") + ".env")
                values = {"credential_file": str(credential), **overrides}
                target = values.pop("expected_user_id", user_id)
                outcome = tool.run_reset(self.reset_args(target, **values), self.runtime)
                self.assertEqual(outcome.exit_code, code)
                self.assertFalse(credential.exists())
        self.assertEqual(self.db.users, before)
        self.assertEqual(self.db.commits, 0)

    def test_failed_commit_keeps_the_original_credential(self):
        user_id = self.seed()
        self.db.fail_commit = True
        outcome = tool.run_reset(self.reset_args(user_id), self.runtime)
        self.assertEqual(outcome.exit_code, tool.EXIT_DATABASE_UPDATE)
        self.assertTrue(self.context.verify(self.OLD, self.db.users[user_id]["hashed_password"]))
        self.assertFalse((self.credentials / "petrov-reset.env").exists())

    def test_verification_failure_restores_the_original_credential(self):
        user_id = self.seed()
        real_verify = self.context.verify
        with patch.object(self.context, "verify", side_effect=lambda p, h: False if p != self.OLD else real_verify(p, h)):
            outcome = tool.run_reset(self.reset_args(user_id), self.runtime)
        self.assertEqual(outcome.exit_code, tool.EXIT_VERIFICATION_ROLLED_BACK)
        self.assertTrue(self.context.verify(self.OLD, self.db.users[user_id]["hashed_password"]))
        self.assertFalse((self.credentials / "petrov-reset.env").exists())
        audit = self.audit_documents()[0]
        self.assertTrue(audit["rollback_succeeded"])
        self.assertFalse(audit["password_hash_changed"])


# ─── static safety ───────────────────────────────────────────────────────────


class StaticSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(tool.__file__).read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_no_endpoint_scheduler_or_migration(self):
        lowered = self.source.casefold()
        for marker in ("apirouter", "@router", "apscheduler", "op.execute", "create table", "alter table"):
            self.assertNotIn(marker, lowered)
        modules = {alias.name for node in ast.walk(self.tree) if isinstance(node, ast.Import) for alias in node.names}
        modules |= {node.module for node in ast.walk(self.tree) if isinstance(node, ast.ImportFrom) and node.module}
        self.assertFalse({name for name in modules if name.split(".")[0] in {"alembic", "fastapi", "apscheduler"}})

    def test_no_weak_randomness_hardcoded_location_or_password_option(self):
        imported = {alias.name for node in ast.walk(self.tree) if isinstance(node, ast.Import) for alias in node.names}
        imported |= {node.module for node in ast.walk(self.tree) if isinstance(node, ast.ImportFrom) and node.module}
        self.assertNotIn("random", imported)
        self.assertNotIn("AgroSat_secrets", self.source)
        self.assertNotIn("add_argument(\"--password", self.source)

    def test_secrets_are_never_printed_or_logged(self):
        self.assertNotIn("logging", self.source)
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "print":
                names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                self.assertTrue(names.isdisjoint({"password", "written_hash", "new_hash", "previous_hash",
                                                  "stored_hash", "token"}), names)

    def test_no_lazy_relationship_access(self):
        attributes = {node.attr for node in ast.walk(self.tree) if isinstance(node, ast.Attribute)}
        self.assertNotIn("enterprise", attributes)


if __name__ == "__main__":
    unittest.main()
