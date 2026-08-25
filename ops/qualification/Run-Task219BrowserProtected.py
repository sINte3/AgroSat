"""Run browser qualification with credentials confined to child memory."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import secrets
import subprocess

from dotenv import dotenv_values
from passlib.context import CryptContext
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


PREFIX = "agrosat_r3_task219_"
SENSITIVE = re.compile(
    r"(?i)(secret|token|password|api.?key|database.?url|authorization|cookie|pgpass)"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-runtime-env", type=Path, required=True)
    parser.add_argument("--pilot-credential-env", type=Path, required=True)
    parser.add_argument("--database-name", required=True)
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.database_name == "agrosat" or not args.database_name.startswith(PREFIX):
        raise RuntimeError("TASK219_DATABASE_IDENTITY_REJECTED")
    if not args.command:
        raise RuntimeError("TASK219_BROWSER_COMMAND_MISSING")
    runtime = {
        key: str(value)
        for key, value in dotenv_values(args.source_runtime_env.resolve(strict=True)).items()
        if value is not None
    }
    pilot = {
        key: str(value)
        for key, value in dotenv_values(args.pilot_credential_env.resolve(strict=True)).items()
        if value is not None
    }
    email_keys = [key for key in pilot if "EMAIL" in key.upper() or "USERNAME" in key.upper()]
    password_keys = [key for key in pilot if "PASSWORD" in key.upper()]
    if len(email_keys) != 1 or len(password_keys) != 1:
        raise RuntimeError("PILOT_CREDENTIAL_VARIABLE_IDENTITY_UNEXPECTED")
    source = make_url(runtime.get("DATABASE_URL") or runtime.get("SUPABASE_DATABASE_URL") or "")
    if source.database != "agrosat":
        raise RuntimeError("TASK219_SOURCE_DATABASE_IDENTITY_REJECTED")
    isolated = source.set(database=args.database_name)
    engine = create_engine(isolated)
    fixture_password = secrets.token_urlsafe(32)
    fixture_hash = CryptContext(schemes=["bcrypt"], deprecated="auto").hash(fixture_password)
    fixture_emails = [
        "task219-browser-enterprise-a@invalid.example",
        "task219-browser-enterprise-b@invalid.example",
    ]
    fixture_ids: list[int] = []
    try:
        with engine.begin() as connection:
            enterprises = [
                int(row[0]) for row in connection.execute(
                    text("SELECT id FROM enterprises ORDER BY id LIMIT 2")
                ).fetchall()
            ]
            if len(enterprises) != 2:
                raise RuntimeError("TASK219_TWO_ENTERPRISE_FIXTURE_REQUIRED")
            for email, enterprise_id in zip(fixture_emails, enterprises, strict=True):
                user_id = connection.execute(text("""
                    INSERT INTO users
                      (enterprise_id,email,full_name,role,hashed_password,is_active,created_at)
                    VALUES (:enterprise,:email,'TASK 219 browser tenant','agronomist',:hash,true,now())
                    ON CONFLICT (email) DO UPDATE SET enterprise_id=excluded.enterprise_id,
                      role='agronomist',hashed_password=excluded.hashed_password,is_active=true
                    RETURNING id
                """), {"enterprise": enterprise_id, "email": email, "hash": fixture_hash}).scalar_one()
                fixture_ids.append(int(user_id))
        environment = {
            key: value for key, value in os.environ.items() if not SENSITIVE.search(key)
        }
        environment.update({
            "TASK219_BROWSER_ADMIN_USERNAME": pilot[email_keys[0]],
            "TASK219_BROWSER_ADMIN_PASSWORD": pilot[password_keys[0]],
            "TASK219_BROWSER_TENANT_A_USERNAME": fixture_emails[0],
            "TASK219_BROWSER_TENANT_B_USERNAME": fixture_emails[1],
            "TASK219_BROWSER_TENANT_PASSWORD": fixture_password,
            "TASK219_BROWSER_DATABASE_IDENTITY": args.database_name,
        })
        completed = subprocess.run(
            args.command,
            cwd=args.cwd.resolve(strict=True),
            env=environment,
            stdin=subprocess.DEVNULL,
        )
        environment.clear()
        return completed.returncode
    finally:
        with engine.begin() as connection:
            if fixture_ids:
                connection.execute(
                    text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": fixture_ids}
                )
        fixture_password = ""
        fixture_hash = ""
        runtime.clear()
        pilot.clear()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
