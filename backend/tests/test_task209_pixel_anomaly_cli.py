import argparse
from datetime import date
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from scripts import process_pixel_anomalies as cli
from services.pixel_anomaly_algorithm import PixelScene
from services.pixel_scene_provider import PixelSceneUnavailable


def grid(value):
    return [[value for _ in range(8)] for _ in range(8)]


def fixture_scene(observed_at, record_id, current):
    values = grid(0.6 if current else 0.62)
    if current:
        anomaly = 0.2
    else:
        anomaly = 0.55
    for row, column in ((2, 2), (2, 3), (3, 2), (3, 3)):
        values[row][column] = anomaly
    return {
        "fixture_id": f"scene-{record_id}",
        "enterprise_id": 7,
        "field_id": 11,
        "index_code": "ndvi",
        "record_type": "ndvi_record",
        "record_id": record_id,
        "observed_at": observed_at,
        "values": values,
        "quality_mask": grid(True),
        "field_mask": grid(True),
        "bbox": [64.0, 39.0, 64.01, 39.01],
        "cloud_cover_pct": 5,
        "valid_pixels_pct": 100,
    }


def write_fixture(path: Path):
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pairs": [
                    {
                        "current": fixture_scene("2026-06-20", 20, True),
                        "comparison": fixture_scene("2026-06-10", 10, False),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def write_requests(path: Path):
    identity = {
        "enterprise_id": 7,
        "field_id": 11,
        "index_code": "ndvi",
        "record_type": "ndvi_record",
    }
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pairs": [
                    {
                        "current": {
                            **identity,
                            "record_id": 20,
                            "observed_at": "2026-06-20",
                        },
                        "comparison": {
                            **identity,
                            "record_id": 10,
                            "observed_at": "2026-06-10",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def args(**changes):
    values = {
        "index": "ndvi",
        "provider": "sentinel_numeric_pixels",
        "fixture": None,
        "request_file": None,
        "write": False,
        "max_fields": 100,
        "timeout_seconds": 30,
        "attempts": 3,
        "checkpoint": None,
        "resume": False,
        "log_file": None,
        "lock_file": None,
    }
    values.update(changes)
    return argparse.Namespace(**values)


def live_scene(observed_at, record_id, current):
    payload = fixture_scene(observed_at.isoformat(), record_id, current)
    return PixelScene(
        enterprise_id=payload["enterprise_id"],
        field_id=payload["field_id"],
        index_code=payload["index_code"],
        record_type=payload["record_type"],
        record_id=payload["record_id"],
        observed_at=observed_at,
        values=tuple(tuple(row) for row in payload["values"]),
        quality_mask=tuple(tuple(row) for row in payload["quality_mask"]),
        field_mask=tuple(tuple(row) for row in payload["field_mask"]),
        bbox=tuple(payload["bbox"]),
        cloud_cover_pct=5,
        valid_pixels_pct=100,
        provider="contract_provider",
        provenance={"provider": "contract"},
    )


def test_fixture_dry_run_is_deterministic_checkpointed_and_resumable(tmp_path):
    fixture = tmp_path / "pixels.json"
    checkpoint = tmp_path / "checkpoint.json"
    write_fixture(fixture)
    invocation = args(fixture=str(fixture), checkpoint=str(checkpoint))
    first_code, first = cli.execute(invocation, cancel_event=Event())
    resumed_code, resumed = cli.execute(
        args(
            fixture=str(fixture),
            checkpoint=str(checkpoint),
            resume=True,
        ),
        cancel_event=Event(),
    )
    assert first_code == 0
    assert first["mode"] == "dry_run"
    assert first["processed"] == 1
    assert first["status_counts"] == {"detected": 1}
    assert first["zones"] == 1
    assert first["inserted_runs"] == 0
    assert resumed_code == 0
    assert resumed["processed"] == 0
    assert resumed["skipped_checkpoint"] == 1
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert len(saved["completed"]) == 1


def test_fixture_write_is_rejected_before_session_creation(tmp_path):
    fixture = tmp_path / "pixels.json"
    write_fixture(fixture)
    session_factory = Mock()
    with pytest.raises(cli.CliContractError, match="rejected"):
        cli.execute(
            args(
                fixture=str(fixture),
                write=True,
                checkpoint=str(tmp_path / "checkpoint.json"),
                log_file=str(tmp_path / "run.json"),
            ),
            cancel_event=Event(),
            session_factory=session_factory,
        )
    session_factory.assert_not_called()


def test_malformed_fixture_is_a_sanitized_contract_failure(tmp_path):
    fixture = tmp_path / "invalid.json"
    fixture.write_text(
        json.dumps({"schema_version": 1, "pairs": [{"current": {}}]}),
        encoding="utf-8",
    )
    with pytest.raises(cli.CliContractError, match="invalid pair"):
        cli.execute(args(fixture=str(fixture)), cancel_event=Event())


def test_live_provider_unsupported_data_is_explicit_and_read_only(tmp_path):
    requests = tmp_path / "requests.json"
    write_requests(requests)
    code, summary = cli.execute(
        args(request_file=str(requests)),
        cancel_event=Event(),
    )
    assert code == 4
    assert summary["processed"] == 0
    assert summary["failure_categories"] == {"unsupported_data": 1}
    assert summary["inserted_runs"] == 0


def test_write_path_uses_real_provider_boundary_and_one_field_transaction(tmp_path):
    requests = tmp_path / "requests.json"
    checkpoint = tmp_path / "checkpoint.json"
    log = tmp_path / "run.json"
    write_requests(requests)
    scenes = {
        (11, date(2026, 6, 20)): live_scene(date(2026, 6, 20), 20, True),
        (11, date(2026, 6, 10)): live_scene(date(2026, 6, 10), 10, False),
    }

    class Provider:
        name = "contract_provider"

        def fetch(self, request, **_kwargs):
            return scenes[(request.field_id, request.observed_at)]

    session = Mock()
    outcome = SimpleNamespace(inserted=True, zone_count=1)
    with (
        patch.object(cli, "provider_registry", return_value={"contract": Provider()}),
        patch.object(cli, "persist_anomaly_result", return_value=outcome) as persist,
    ):
        code, summary = cli.execute(
            args(
                request_file=str(requests),
                provider="contract",
                write=True,
                checkpoint=str(checkpoint),
                log_file=str(log),
            ),
            cancel_event=Event(),
            session_factory=Mock(return_value=session),
        )
    assert code == 0
    assert summary["inserted_runs"] == 1
    assert summary["zones"] == 1
    persist.assert_called_once()
    assert persist.call_args.kwargs["processing_run_id"] == summary["run_id"]
    session.close.assert_called_once()
    assert json.loads(log.read_text(encoding="utf-8"))["exit_code"] == 0


def test_retry_is_bounded_and_only_for_transient_categories():
    request = cli.PixelSceneRequest(
        7,
        11,
        "ndvi",
        date(2026, 6, 20),
        "ndvi_record",
        20,
    )

    class Flaky:
        name = "flaky"

        def __init__(self):
            self.calls = 0

        def fetch(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls < 3:
                raise PixelSceneUnavailable("network", "bounded fixture error")
            return "scene"

    provider = Flaky()
    sleeps = []
    assert (
        cli.fetch_with_retry(
            provider,
            request,
            timeout_seconds=30,
            attempts=3,
            cancel_event=Event(),
            sleep=sleeps.append,
        )
        == "scene"
    )
    assert provider.calls == 3
    assert sleeps == [1, 2]


def test_cli_source_has_no_web_scheduler_or_fixture_apply_fallback():
    source = Path(cli.__file__).read_text(encoding="utf-8")
    assert "APScheduler" not in source
    assert "BackgroundScheduler" not in source
    assert "AsyncIOScheduler" not in source
    assert "FastAPI" not in source
    assert "fixture input is rejected with --write" in source
    assert "MAX_FIELDS = 100" in source
    assert "MAX_ATTEMPTS = 5" in source
    assert "MUTEX_NAME" in source
