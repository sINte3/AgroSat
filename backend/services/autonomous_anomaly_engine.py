"""Deterministic explainable anomaly policy for autonomous monitoring.

This module is deliberately statistical and contains no ML or generative model.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from statistics import median
from typing import Iterable, Sequence


RULE_VERSION = "r3-e-v1"
SUPPORTED_INDICES = ("ndvi", "savi", "evi", "ndmi", "ndre")


@dataclass(frozen=True)
class RulePolicy:
    min_baseline_scenes: int = 5
    min_persistence_scenes: int = 2
    min_area_ha: float = 0.25
    min_area_fraction: float = 0.01
    moderate_confidence: float = 0.60
    high_confidence: float = 0.85
    extreme_deviation: float = 6.0
    supporting_index_agreement: int = 2
    cooldown_days: int = 14
    auto_inspection_cap: int = 20
    max_spike_fraction: float = 0.10
    fresh_days: int = 10
    stale_days: int = 20

    def as_dict(self) -> dict[str, int | float]:
        return dict(sorted(self.__dict__.items()))

    def fingerprint(self) -> str:
        encoded = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RobustSignal:
    baseline_median: float
    mad: float
    magnitude: float
    robust_deviation: float


@dataclass(frozen=True)
class CandidateAssessment:
    source_key: str
    zone_key: str
    score: float
    confidence: float
    severity: str
    eligible_for_automatic_inspection: bool
    review_band: str
    explanation: str
    cooldown_until: datetime


@dataclass(frozen=True)
class InspectionPlan:
    candidate_ids: tuple[int, ...]
    spike_guard_triggered: bool
    suppressed_duplicate_ids: tuple[int, ...]
    suppressed_cap_ids: tuple[int, ...]


def freshness_status(
    accepted_at: datetime | None,
    *,
    now: datetime | None = None,
    last_outcome: str | None = None,
    policy: RulePolicy | None = None,
) -> str:
    policy = policy or RulePolicy()
    now = now or datetime.now(timezone.utc)
    if accepted_at is None:
        return {
            "cloud_blocked": "CLOUD_BLOCKED",
            "provider_degraded": "PROVIDER_DEGRADED",
            "quality_blocked": "QUALITY_BLOCKED",
        }.get(last_outcome or "", "NEVER_COLLECTED")
    if accepted_at.tzinfo is None:
        accepted_at = accepted_at.replace(tzinfo=timezone.utc)
    age = max(0, (now.date() - accepted_at.astimezone(timezone.utc).date()).days)
    if age <= policy.fresh_days:
        return "FRESH"
    if age <= policy.stale_days:
        return "AGING"
    return "STALE"


def robust_signal(history: Sequence[float], current: float, policy: RulePolicy | None = None) -> RobustSignal:
    policy = policy or RulePolicy()
    values = [float(value) for value in history]
    if len(values) < policy.min_baseline_scenes:
        raise ValueError("at least five valid baseline scenes are required")
    if any(not math.isfinite(value) or not -1 <= value <= 1 for value in (*values, current)):
        raise ValueError("observations must be finite and in [-1, 1]")
    center = median(values)
    mad = median(abs(value - center) for value in values)
    scale = max(mad * 1.4826, 0.02)
    magnitude = max(0.0, center - current)
    return RobustSignal(
        baseline_median=round(center, 6),
        mad=round(mad, 6),
        magnitude=round(magnitude, 6),
        robust_deviation=round(magnitude / scale, 6),
    )


def stable_keys(
    *, enterprise_id: int, field_id: int, provider: str, index_code: str,
    geometry_hash: str, rule_version: str = RULE_VERSION,
) -> tuple[str, str]:
    if index_code not in SUPPORTED_INDICES:
        raise ValueError("unsupported index")
    if not geometry_hash or len(geometry_hash) != 64:
        raise ValueError("geometry_hash must be SHA-256")
    zone_key = hashlib.sha256(f"{field_id}|{geometry_hash}".encode()).hexdigest()
    source_key = hashlib.sha256(
        f"{enterprise_id}|{field_id}|{provider}|{index_code}|{zone_key}|{rule_version}".encode()
    ).hexdigest()
    return source_key, zone_key


def assess_candidate(
    *, enterprise_id: int, field_id: int, provider: str, index_code: str,
    geometry_hash: str, history: Sequence[float], current: float,
    affected_area_ha: float, field_area_ha: float, persistence_scenes: int,
    supporting_agreement: int, data_quality: float,
    acquired_at: datetime, policy: RulePolicy | None = None,
) -> CandidateAssessment | None:
    policy = policy or RulePolicy()
    if field_area_ha <= 0 or affected_area_ha <= 0:
        raise ValueError("field and affected areas must be positive")
    if not 0 <= data_quality <= 1 or not 0 <= supporting_agreement <= 4:
        raise ValueError("quality/agreement is outside the bounded contract")
    minimum_area = max(policy.min_area_ha, field_area_ha * policy.min_area_fraction)
    if affected_area_ha + 1e-9 < minimum_area:
        return None
    signal = robust_signal(history, current, policy)
    if signal.magnitude < 0.08 or signal.robust_deviation < 3.0:
        return None
    magnitude_score = min(1.0, signal.magnitude / 0.30)
    deviation_score = min(1.0, signal.robust_deviation / policy.extreme_deviation)
    persistence_score = min(1.0, persistence_scenes / policy.min_persistence_scenes)
    agreement_score = min(1.0, supporting_agreement / max(1, policy.supporting_index_agreement))
    confidence = round(
        0.25 * magnitude_score + 0.25 * deviation_score +
        0.20 * persistence_score + 0.15 * agreement_score + 0.15 * data_quality,
        6,
    )
    score = round(0.60 * deviation_score + 0.40 * magnitude_score, 6)
    extreme = signal.robust_deviation >= policy.extreme_deviation and confidence >= policy.high_confidence
    persistent = persistence_scenes >= policy.min_persistence_scenes
    automatic = confidence >= policy.high_confidence and (
        (persistent and supporting_agreement >= policy.supporting_index_agreement) or extreme
    )
    review_band = "high" if automatic else "moderate" if confidence >= policy.moderate_confidence else "low"
    severity = "EXTREME" if extreme else "HIGH" if confidence >= policy.high_confidence else "MODERATE" if confidence >= policy.moderate_confidence else "LOW"
    source_key, zone_key = stable_keys(
        enterprise_id=enterprise_id, field_id=field_id, provider=provider,
        index_code=index_code, geometry_hash=geometry_hash,
    )
    reason = "extreme single valid scene" if extreme and not persistent else f"persistent across {persistence_scenes} valid scenes" if persistent else "single-scene candidate retained for review"
    explanation = (
        f"{index_code.upper()} is {signal.magnitude:.3f} below the field rolling median "
        f"({signal.robust_deviation:.2f} robust deviations); {affected_area_ha:.2f} ha affected, "
        f"{supporting_agreement} supporting indices agree, quality {data_quality:.0%}; {reason}."
    )
    return CandidateAssessment(
        source_key=source_key, zone_key=zone_key, score=score,
        confidence=confidence, severity=severity,
        eligible_for_automatic_inspection=automatic, review_band=review_band,
        explanation=explanation,
        cooldown_until=acquired_at + timedelta(days=policy.cooldown_days),
    )


def plan_automatic_inspections(
    candidates: Iterable[dict], *, active_field_count: int,
    open_source_zone_keys: set[tuple[str, str]] | None = None,
    now: datetime | None = None, policy: RulePolicy | None = None,
) -> InspectionPlan:
    policy = policy or RulePolicy()
    now = now or datetime.now(timezone.utc)
    eligible = [item for item in candidates if item.get("automatic")]
    triggered_fields = {int(item["field_id"]) for item in eligible}
    if active_field_count <= 0:
        raise ValueError("active_field_count must be positive")
    if len(triggered_fields) / active_field_count > policy.max_spike_fraction:
        return InspectionPlan((), True, (), tuple(sorted(int(item["id"]) for item in eligible)))
    existing = set(open_source_zone_keys or ())
    selected: list[int] = []
    duplicates: list[int] = []
    cap: list[int] = []
    ordered = sorted(eligible, key=lambda item: (-float(item["confidence"]), int(item["id"])))
    for item in ordered:
        key = (str(item["source_key"]), str(item["zone_key"]))
        cooldown = item.get("cooldown_until")
        if key in existing or (isinstance(cooldown, datetime) and cooldown > now and item.get("replay")):
            duplicates.append(int(item["id"]))
            continue
        if len(selected) >= policy.auto_inspection_cap:
            cap.append(int(item["id"]))
            continue
        selected.append(int(item["id"]))
        existing.add(key)
    return InspectionPlan(tuple(selected), False, tuple(duplicates), tuple(cap))


def cleanup_mask(
    candidate_mask: Sequence[Sequence[bool]], valid_mask: Sequence[Sequence[bool]],
    *, minimum_pixels: int = 4,
) -> tuple[tuple[bool, ...], ...]:
    """Remove invalid/edge pixels and bounded connected noise deterministically."""
    height = len(candidate_mask)
    width = len(candidate_mask[0]) if height else 0
    if height < 3 or width < 3 or len(valid_mask) != height:
        raise ValueError("mask must be matching rectangular matrices of at least 3x3")
    if any(len(row) != width for row in (*candidate_mask, *valid_mask)):
        raise ValueError("mask must be matching rectangular matrices")
    eligible = {
        (row, col) for row in range(1, height - 1) for col in range(1, width - 1)
        if candidate_mask[row][col] and valid_mask[row][col]
    }
    kept: set[tuple[int, int]] = set()
    remaining = set(eligible)
    while remaining:
        start = min(remaining)
        remaining.remove(start)
        queue = deque([start])
        component = {start}
        while queue:
            row, col = queue.popleft()
            for neighbor in ((row-1,col),(row+1,col),(row,col-1),(row,col+1)):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.add(neighbor)
                    queue.append(neighbor)
        if len(component) >= minimum_pixels:
            kept.update(component)
    return tuple(tuple((row, col) in kept for col in range(width)) for row in range(height))
