"""Standalone productivity-zone CLI contract tests."""

import argparse
import json
from pathlib import Path
from threading import Event

import pytest

from scripts import compute_productivity_zones as cli


def fixture(path: Path):
    measurements = []
    for season_index, season in enumerate((2024, 2025, 2026)):
        for index in range(24):
            measurements.append({
                "import_id": 100 + season_index,
                "source_sha256": f"{season_index + 1:064x}",
                "season_year": season,
                "longitude": 64.42 + (index % 6) * 0.00035,
                "latitude": 39.77 + (index // 6) * 0.00027,
                "yield_t_ha": (
                    3.0
                    + (index % 6) * 0.35
                    + (index // 6) * 0.1
                    + season_index * 0.05
                ),
            })
    path.write_text(json.dumps({
        "schema_version": 1,
        "fields": [{
            "enterprise_id": 5,
            "field_id": 3,
            "measurements": measurements,
        }],
    }), encoding="utf-8")


def args(**changes):
    values = {
        "enterprise_id": None,
        "field": None,
        "season": None,
        "fixture": None,
        "write": False,
        "max_fields": 100,
        "checkpoint": None,
        "resume": False,
        "log_file": None,
        "lock_file": None,
    }
    values.update(changes)
    return argparse.Namespace(**values)


def test_fixture_dry_run_is_deterministic_checkpointed_and_resumable(tmp_path):
    source = tmp_path / "fixture.json"
    checkpoint = tmp_path / "checkpoint.json"
    fixture(source)
    invocation = args(fixture=str(source), checkpoint=str(checkpoint))
    first_code, first = cli.execute(invocation, cancel_event=Event())
    second_code, second = cli.execute(invocation, cancel_event=Event())
    assert first_code == second_code == 0
    assert first["status_counts"] == second["status_counts"] == {"ready": 1}
    assert first["processed"] == second["processed"] == 1
    resumed_code, resumed = cli.execute(
        args(
            fixture=str(source),
            checkpoint=str(checkpoint),
            resume=True,
        ),
        cancel_event=Event(),
    )
    assert resumed_code == 0
    assert resumed["processed"] == 0
    assert resumed["skipped_checkpoint"] == 1
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert len(saved["completed"]) == 1
    assert len(next(iter(saved["completed"].values()))["run_key"]) == 64


def test_fixture_is_rejected_in_write_mode(tmp_path):
    source = tmp_path / "fixture.json"
    fixture(source)
    with pytest.raises(cli.CliContractError, match="rejected with --write"):
        cli.execute(
            args(
                fixture=str(source),
                write=True,
                checkpoint=str(tmp_path / "checkpoint.json"),
                log_file=str(tmp_path / "log.json"),
            ),
            cancel_event=Event(),
        )


def test_batch_seasons_paths_and_source_are_fail_closed(tmp_path):
    with pytest.raises(cli.CliContractError, match="provide --fixture"):
        cli.execute(args(), cancel_event=Event())
    with pytest.raises(cli.CliContractError, match="five"):
        cli.execute(
            args(
                enterprise_id=5,
                field=[3],
                season=[2020, 2021, 2022, 2023, 2024, 2025],
            ),
            cancel_event=Event(),
        )
    with pytest.raises(cli.CliContractError, match="outside the repository"):
        cli.execute(
            args(fixture=str(cli.REPO_ROOT / "fixture.json")),
            cancel_event=Event(),
        )


def test_database_dry_run_and_write_delegate_bounded_fields(monkeypatch, tmp_path):
    calls = []

    class Db:
        closed = False

        def close(self):
            self.closed = True

    db = Db()

    def compute(_db, enterprise_id, field_id, **kwargs):
        calls.append((enterprise_id, field_id, kwargs))
        return {
            "created": kwargs["write"],
            "run_id": 1,
            "run_key": "a" * 64,
            "status": "ready",
        }

    monkeypatch.setattr(cli, "compute_field", compute)
    code, summary = cli.execute(
        args(
            enterprise_id=5,
            field=[4, 3, 3],
            season=[2026, 2024, 2025],
            write=True,
            checkpoint=str(tmp_path / "checkpoint.json"),
            log_file=str(tmp_path / "log.json"),
        ),
        cancel_event=Event(),
        session_factory=lambda: db,
    )
    assert code == 0
    assert summary["processed"] == summary["created_runs"] == 2
    assert [call[1] for call in calls] == [3, 4]
    assert all(call[2]["write"] is True for call in calls)
    assert all(call[2]["seasons"] == (2024, 2025, 2026) for call in calls)
    assert db.closed is True
    logged = json.loads((tmp_path / "log.json").read_text(encoding="utf-8"))
    assert logged["failure_categories"] == {}


def test_cli_has_single_instance_cancellation_and_sanitized_errors():
    source = Path(cli.__file__).read_text(encoding="utf-8")
    assert "acquire_lock" in source and "release_lock" in source
    assert "SIGINT" in source and "SIGTERM" in source
    assert "Global\\\\AgroSatProductivityZones_v1" in source
    sanitized = cli.sanitize_text(
        "password=do-not-show postgresql://name:value@server/db"
    )
    assert "do-not-show" not in sanitized
    assert "name:value" not in sanitized
