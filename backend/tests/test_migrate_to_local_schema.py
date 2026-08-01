"""Migration-boundary tests for the one-time local database transfer tool."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from scripts import migrate_to_local


def test_local_schema_uses_canonical_alembic_chain(monkeypatch):
    monkeypatch.setenv("AGROSAT_RUNTIME_ENV_FILE", "C:/unrelated/runtime.env")
    completed = subprocess.CompletedProcess(args=[], returncode=0)

    with patch.object(migrate_to_local.subprocess, "run", return_value=completed) as run:
        migrate_to_local.init_local_schema("postgresql://local-only/db")

    command = run.call_args.args[0]
    kwargs = run.call_args.kwargs
    assert command[-2:] == ["upgrade", "head"]
    assert command[:3] == [migrate_to_local.sys.executable, "-m", "alembic"]
    assert Path(kwargs["cwd"]) == migrate_to_local.BACKEND_DIR
    assert kwargs["env"]["DATABASE_URL"] == "postgresql://local-only/db"
    assert "AGROSAT_RUNTIME_ENV_FILE" not in kwargs["env"]
    assert kwargs["capture_output"] is True
    assert kwargs["check"] is False


def test_local_schema_failure_does_not_echo_alembic_output(capsys):
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="sensitive connection context",
        stderr="sensitive provider detail",
    )

    with patch.object(migrate_to_local.subprocess, "run", return_value=completed):
        with pytest.raises(SystemExit):
            migrate_to_local.init_local_schema("postgresql://local-only/db")

    output = capsys.readouterr().out
    assert "sensitive connection context" not in output
    assert "sensitive provider detail" not in output
    assert "Alembic migration chain failed" in output


def test_local_transfer_tool_never_uses_metadata_create_all():
    source = Path(migrate_to_local.__file__).read_text(encoding="utf-8")
    assert "metadata.create_all" not in source
    assert "Database error: {" not in source
