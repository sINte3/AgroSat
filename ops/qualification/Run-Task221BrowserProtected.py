"""Run TASK_221 browser checks with the pilot credential confined to child memory."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path

from dotenv import dotenv_values


DATABASE_NAME = re.compile(r"^agrosat_r3_task221_[a-z0-9_]+$")
SENSITIVE_NAME = re.compile(
    r"(?i)(secret|token|password|api.?key|database.?url|authorization|cookie|pgpass)"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot-credential-env", type=Path, required=True)
    parser.add_argument("--database-name", required=True)
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.database_name == "agrosat" or not DATABASE_NAME.fullmatch(args.database_name):
        raise RuntimeError("TASK221_DATABASE_IDENTITY_REJECTED")
    values = {
        key: str(value)
        for key, value in dotenv_values(
            args.pilot_credential_env.resolve(strict=True)
        ).items()
        if value is not None
    }
    emails = [key for key in values if "EMAIL" in key.upper() or "USERNAME" in key.upper()]
    passwords = [key for key in values if "PASSWORD" in key.upper()]
    if len(emails) != 1 or len(passwords) != 1 or not args.command:
        raise RuntimeError("TASK221_BROWSER_CREDENTIAL_CONTRACT_REJECTED")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not SENSITIVE_NAME.search(key)
    }
    environment.update(
        {
            "TASK221_BROWSER_USERNAME": values[emails[0]],
            "TASK221_BROWSER_PASSWORD": values[passwords[0]],
            "TASK221_BROWSER_DATABASE_IDENTITY": args.database_name,
        }
    )
    try:
        completed = subprocess.run(
            args.command,
            cwd=args.cwd.resolve(strict=True),
            env=environment,
            stdin=subprocess.DEVNULL,
        )
        return completed.returncode
    finally:
        environment.clear()
        values.clear()


if __name__ == "__main__":
    raise SystemExit(main())
