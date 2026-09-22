"""Canonical accepted-satellite-observation quality contract.

This module is the single source of truth for deciding whether a persisted
satellite observation is good enough to be used as operational evidence. It
covers both observation tables: ``ndvi_records`` (``mean_ndvi``) and
``satellite_index_records`` (``mean_value``).

Why ``cloud_cover_pct`` is allowed to be NULL
---------------------------------------------
The canonical Sentinel-2 collectors never populate ``cloud_cover_pct``. Cloud
rejection already happens one level earlier, inside the evalscript: the Scene
Classification Layer mask discards cloud, cloud shadow, cirrus, snow and other
invalid scene classes, and ``valid_pixels_pct`` is computed *after* that mask.
The share of pixels that survives the mask is therefore the real, per-pixel
quality signal for these observations, and this contract keeps it mandatory.

A NULL ``cloud_cover_pct`` means exactly one thing: "this separate scene-level
metadata value was not measured". It does not mean "cloud free" and it does not
mean "cloud blocked". Both of the alternative readings are wrong:

* Treating NULL as bad (the defect this module repairs) rejected every real
  observation, which left all 1,375 production field/index pairs permanently
  ``NEVER_COLLECTED`` even though collection was succeeding.
* Treating NULL as 0 would assert a cloud-free scene that nobody measured.

So NULL is neutral. It neither accepts nor rejects on its own, and acceptance
rests on the index value and on ``valid_pixels_pct``.

If a provider ever does supply a trustworthy scene-level cloud percentage it
remains an additional quality signal and is enforced here: a measured value
above :data:`MAX_CLOUD_COVER_PCT` rejects the observation. Metadata that is
present but malformed is rejected conservatively rather than being silently
reinterpreted as "not measured".

Two valid-pixel tiers exist because the two workflows that consume observations
were specified with different minimums. They are preserved deliberately rather
than flattened: see :data:`MIN_VALID_PIXELS_FRESHNESS_PCT` and
:data:`MIN_VALID_PIXELS_ANALYSIS_PCT`.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re


# ─── Thresholds ──────────────────────────────────────────────────────────────

#: Highest measured scene cloud percentage that still yields an accepted
#: observation. Only applied when cloud metadata is actually present.
MAX_CLOUD_COVER_PCT = 30.0

#: Minimum share of valid (SCL-masked) pixels for satellite freshness, as
#: specified by TASK_219 autonomous monitoring.
MIN_VALID_PIXELS_FRESHNESS_PCT = 60.0

#: Minimum share of valid (SCL-masked) pixels for analytical consumers:
#: closed-loop agronomy baselines and verification, executive accountability
#: and the legacy operational-closure verification engine.
MIN_VALID_PIXELS_ANALYSIS_PCT = 50.0

#: Supported vegetation indices are normalised ratios.
MIN_INDEX_VALUE = -1.0
MAX_INDEX_VALUE = 1.0

#: A percentage is only meaningful inside these bounds. Values outside them are
#: malformed metadata, not extreme measurements.
MIN_PERCENTAGE = 0.0
MAX_PERCENTAGE = 100.0


# ─── Verdict vocabulary ──────────────────────────────────────────────────────

ACCEPTED = "accepted"
REJECTED_CLOUD = "rejected_cloud"
REJECTED_QUALITY = "rejected_quality"
MISSING = "missing"

#: How to read the scene-level cloud metadata of one observation.
CLOUD_UNAVAILABLE = "unavailable"
CLOUD_MEASURED = "measured"
CLOUD_MALFORMED = "malformed"


@dataclass(frozen=True, slots=True)
class QualityVerdict:
    """Why one observation was accepted or rejected.

    ``detail`` is a short operator-facing explanation. It contains only
    thresholds and observation statistics, never credentials or provider
    payloads.
    """

    accepted: bool
    reason: str
    cloud_metadata: str
    detail: str


def _finite(value: object) -> float | None:
    """Return ``value`` as a finite float, or ``None`` when it is not one.

    ``None``, non-numeric input, NaN and both infinities all collapse to
    ``None`` so that every caller handles "no usable number" the same way.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def classify_cloud_metadata(cloud_cover_pct: object) -> str:
    """Classify the scene-level cloud metadata of one observation.

    ``CLOUD_UNAVAILABLE`` is the normal case for the canonical collectors.
    ``CLOUD_MALFORMED`` covers metadata that is present but unusable: NaN,
    infinity, negative percentages and percentages above 100.
    """
    if cloud_cover_pct is None:
        return CLOUD_UNAVAILABLE
    number = _finite(cloud_cover_pct)
    if number is None:
        return CLOUD_MALFORMED
    if not MIN_PERCENTAGE <= number <= MAX_PERCENTAGE:
        return CLOUD_MALFORMED
    return CLOUD_MEASURED


def evaluate(
    *,
    value: object,
    valid_pixels_pct: object,
    cloud_cover_pct: object,
    minimum_valid_pixels_pct: float,
) -> QualityVerdict:
    """Evaluate one observation against the canonical quality contract.

    The check order matches the precedence the agronomy verification policy has
    always used: a measured, excessive cloud percentage is reported as a cloud
    rejection even when the observation would also fail another check.
    """
    threshold = float(minimum_valid_pixels_pct)
    cloud_state = classify_cloud_metadata(cloud_cover_pct)
    cloud_number = _finite(cloud_cover_pct)

    # Measured and too cloudy. Reported first so the reason stays specific.
    if cloud_state == CLOUD_MEASURED and cloud_number > MAX_CLOUD_COVER_PCT:
        return QualityVerdict(
            accepted=False,
            reason=REJECTED_CLOUD,
            cloud_metadata=cloud_state,
            detail=(
                f"measured cloud cover {cloud_number:.1f}% exceeds "
                f"{MAX_CLOUD_COVER_PCT:.1f}%"
            ),
        )

    index_value = _finite(value)
    if index_value is None:
        return QualityVerdict(
            accepted=False,
            reason=REJECTED_QUALITY,
            cloud_metadata=cloud_state,
            detail="index value is missing or not a finite number",
        )
    if not MIN_INDEX_VALUE <= index_value <= MAX_INDEX_VALUE:
        return QualityVerdict(
            accepted=False,
            reason=REJECTED_QUALITY,
            cloud_metadata=cloud_state,
            detail=(
                f"index value {index_value:.4f} is outside "
                f"[{MIN_INDEX_VALUE:.1f}, {MAX_INDEX_VALUE:.1f}]"
            ),
        )

    valid_pixels = _finite(valid_pixels_pct)
    if valid_pixels is None:
        return QualityVerdict(
            accepted=False,
            reason=REJECTED_QUALITY,
            cloud_metadata=cloud_state,
            detail="valid pixel percentage is missing or not a finite number",
        )
    if not MIN_PERCENTAGE <= valid_pixels <= MAX_PERCENTAGE:
        return QualityVerdict(
            accepted=False,
            reason=REJECTED_QUALITY,
            cloud_metadata=cloud_state,
            detail=f"valid pixel percentage {valid_pixels:.1f}% is not a percentage",
        )
    if valid_pixels < threshold:
        return QualityVerdict(
            accepted=False,
            reason=REJECTED_QUALITY,
            cloud_metadata=cloud_state,
            detail=(
                f"valid pixel percentage {valid_pixels:.1f}% is below the "
                f"required {threshold:.1f}%"
            ),
        )

    # Cloud metadata that is present but unusable is rejected rather than
    # silently reinterpreted as "not measured".
    if cloud_state == CLOUD_MALFORMED:
        return QualityVerdict(
            accepted=False,
            reason=REJECTED_QUALITY,
            cloud_metadata=cloud_state,
            detail="cloud metadata is present but not a usable percentage",
        )

    if cloud_state == CLOUD_UNAVAILABLE:
        detail = (
            "accepted on SCL-masked valid pixels; scene cloud metadata was "
            "not measured"
        )
    else:
        detail = (
            f"accepted; measured cloud cover {cloud_number:.1f}% is within "
            f"{MAX_CLOUD_COVER_PCT:.1f}%"
        )
    return QualityVerdict(
        accepted=True,
        reason=ACCEPTED,
        cloud_metadata=cloud_state,
        detail=detail,
    )


def is_accepted(
    *,
    value: object,
    valid_pixels_pct: object,
    cloud_cover_pct: object,
    minimum_valid_pixels_pct: float,
) -> bool:
    """Return whether one observation satisfies the canonical contract."""
    return evaluate(
        value=value,
        valid_pixels_pct=valid_pixels_pct,
        cloud_cover_pct=cloud_cover_pct,
        minimum_valid_pixels_pct=minimum_valid_pixels_pct,
    ).accepted


def legacy_blocked_status(verdict: QualityVerdict) -> str | None:
    """Map a verdict onto the agronomy verification status vocabulary.

    ``None`` means accepted. The two rejection strings are the existing
    ``agronomy_plans.verification_status`` values, so persisted state and the
    CHECK constraint that guards it are unchanged by this contract.
    """
    if verdict.accepted:
        return None
    if verdict.reason == REJECTED_CLOUD:
        return "CLOUD_BLOCKED"
    return "QUALITY_BLOCKED"


# ─── SQL predicate ───────────────────────────────────────────────────────────

_SQL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


def _identifier(name: str, *, label: str) -> str:
    """Validate one caller-supplied column reference.

    Every caller passes a literal from this repository, never user input. The
    check exists so that a future caller cannot turn this builder into a string
    concatenation hole.
    """
    if not isinstance(name, str) or not _SQL_IDENTIFIER.fullmatch(name):
        raise ValueError(f"{label} must be a plain SQL column reference")
    return name


def accepted_observation_sql(
    *,
    value_column: str,
    minimum_valid_pixels_pct: float,
    valid_pixels_column: str = "valid_pixels_pct",
    cloud_column: str = "cloud_cover_pct",
    require_cloud_metadata: bool = False,
) -> str:
    """Build the canonical accepted-observation SQL predicate.

    The result is a parenthesised boolean expression that can be appended to a
    ``WHERE`` clause with ``AND``. It carries no bind parameters, so it can be
    embedded in any statement without colliding with the caller's parameter
    names; every number it contains comes from a module constant or from the
    caller's literal threshold.

    PostgreSQL orders NaN above every other float, so a NaN value or NaN cloud
    percentage fails the upper-bound comparison and is rejected, matching
    :func:`evaluate`.

    Set ``require_cloud_metadata`` only where a database constraint forces the
    persisted row to carry a cloud percentage. It makes the predicate stricter
    than the contract and is documented at its single call site.
    """
    value = _identifier(value_column, label="value_column")
    valid = _identifier(valid_pixels_column, label="valid_pixels_column")
    cloud = _identifier(cloud_column, label="cloud_column")
    threshold = float(minimum_valid_pixels_pct)
    if not MIN_PERCENTAGE <= threshold <= MAX_PERCENTAGE:
        raise ValueError("minimum_valid_pixels_pct must be a percentage")

    if require_cloud_metadata:
        cloud_clause = (
            f"{cloud} IS NOT NULL"
            f" AND {cloud} >= {MIN_PERCENTAGE:.1f}"
            f" AND {cloud} <= {MAX_CLOUD_COVER_PCT:.1f}"
        )
    else:
        # NULL is neutral: not measured is neither cloud-free nor cloud-blocked.
        cloud_clause = (
            f"{cloud} IS NULL"
            f" OR ({cloud} >= {MIN_PERCENTAGE:.1f}"
            f" AND {cloud} <= {MAX_CLOUD_COVER_PCT:.1f})"
        )

    return (
        "("
        f"{value} IS NOT NULL"
        f" AND {value} >= {MIN_INDEX_VALUE:.1f}"
        f" AND {value} <= {MAX_INDEX_VALUE:.1f}"
        f" AND {valid} IS NOT NULL"
        f" AND {valid} >= {threshold:.1f}"
        f" AND {valid} <= {MAX_PERCENTAGE:.1f}"
        f" AND ({cloud_clause})"
        ")"
    )
