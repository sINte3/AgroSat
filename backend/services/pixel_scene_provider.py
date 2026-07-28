"""Provider boundary for numeric, masked pixel scenes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from threading import Event
from typing import Any, Protocol

from services.pixel_anomaly_algorithm import PixelScene


class PixelSceneUnavailable(RuntimeError):
    def __init__(self, category: str, detail: str):
        self.category = category
        super().__init__(detail)


@dataclass(frozen=True)
class PixelSceneRequest:
    enterprise_id: int
    field_id: int
    index_code: str
    observed_at: date
    record_type: str
    record_id: int


class PixelSceneProvider(Protocol):
    name: str

    def fetch(
        self,
        request: PixelSceneRequest,
        *,
        timeout_seconds: int,
        cancel_event: Event,
    ) -> PixelScene: ...


class SentinelNumericPixelProvider:
    """Fail-closed boundary until a numeric Sentinel scene contract is configured."""

    name = "sentinel_numeric_pixels"

    def fetch(
        self,
        request: PixelSceneRequest,
        *,
        timeout_seconds: int,
        cancel_event: Event,
    ) -> PixelScene:
        if cancel_event.is_set():
            raise PixelSceneUnavailable("cancelled", "pixel scene request cancelled")
        if not 1 <= timeout_seconds <= 300:
            raise PixelSceneUnavailable("contract", "provider timeout is out of range")
        raise PixelSceneUnavailable(
            "unsupported_data",
            "numeric Sentinel pixel scenes are not configured",
        )


class FixturePixelSceneProvider:
    """Deterministic provider allowed only by explicit dry-run callers."""

    name = "deterministic_fixture"

    def __init__(self, scenes: dict[tuple[int, date], PixelScene]):
        self._scenes = dict(scenes)

    def fetch(
        self,
        request: PixelSceneRequest,
        *,
        timeout_seconds: int,
        cancel_event: Event,
    ) -> PixelScene:
        if cancel_event.is_set():
            raise PixelSceneUnavailable("cancelled", "pixel scene request cancelled")
        if not 1 <= timeout_seconds <= 300:
            raise PixelSceneUnavailable("contract", "provider timeout is out of range")
        scene = self._scenes.get((request.field_id, request.observed_at))
        if scene is None:
            raise PixelSceneUnavailable("unsupported_data", "fixture scene not found")
        expected = (
            request.enterprise_id,
            request.field_id,
            request.index_code,
            request.observed_at,
            request.record_type,
            request.record_id,
        )
        actual = (
            scene.enterprise_id,
            scene.field_id,
            scene.index_code,
            scene.observed_at,
            scene.record_type,
            scene.record_id,
        )
        if actual != expected:
            raise PixelSceneUnavailable("contract", "fixture scene identity mismatch")
        return scene


def provider_registry() -> dict[str, PixelSceneProvider]:
    return {"sentinel_numeric_pixels": SentinelNumericPixelProvider()}


def scene_from_fixture(payload: dict[str, Any]) -> PixelScene:
    """Parse an explicit JSON fixture without accepting partial production data."""
    try:
        return PixelScene(
            enterprise_id=int(payload["enterprise_id"]),
            field_id=int(payload["field_id"]),
            index_code=str(payload["index_code"]).lower(),
            record_type=str(payload["record_type"]),
            record_id=int(payload["record_id"]),
            observed_at=date.fromisoformat(str(payload["observed_at"])),
            values=tuple(tuple(float(value) for value in row) for row in payload["values"]),
            quality_mask=tuple(
                tuple(value if type(value) is bool else _invalid_bool() for value in row)
                for row in payload["quality_mask"]
            ),
            field_mask=tuple(
                tuple(value if type(value) is bool else _invalid_bool() for value in row)
                for row in payload["field_mask"]
            ),
            bbox=tuple(float(value) for value in payload["bbox"]),
            cloud_cover_pct=float(payload["cloud_cover_pct"]),
            valid_pixels_pct=float(payload["valid_pixels_pct"]),
            provider="deterministic_fixture",
            provenance={"fixture_id": str(payload["fixture_id"])},
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PixelSceneUnavailable("contract", "invalid pixel-scene fixture") from exc


def _invalid_bool():
    raise ValueError("mask values must be boolean")
