"""Pure deterministic next-observation eligibility and direction engine."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from services import observation_quality


SUPPORTED_INDEX_CODES = frozenset({"ndvi", "savi", "evi", "ndmi", "ndre"})
ALGORITHM_VERSION = "observation_direction_v1"
# Sourced from the canonical contract so the two cannot drift apart.
QUALITY_VALID_PIXELS_MIN = observation_quality.MIN_VALID_PIXELS_ANALYSIS_PCT
QUALITY_CLOUD_MAX = observation_quality.MAX_CLOUD_COVER_PCT
# Confidence heuristic, not part of the acceptance contract.
HIGH_VALID_PIXELS_MIN = 80.0
HIGH_CLOUD_MAX = 10.0
LIMITATION = (
    "Observed spectral change does not prove that the corrective action caused "
    "the change and is not an agronomic diagnosis."
)


@dataclass(frozen=True, slots=True)
class Observation:
    record_id: int
    source: str
    field_id: int
    index_code: str
    observed_at: date
    value: float
    valid_pixels_pct: float
    # May be None: the canonical collectors do not measure scene-level cloud
    # cover. See services/observation_quality.py.
    cloud_cover_pct: float | None
    satellite: str


def accepted_quality(observation: Observation) -> bool:
    """Whether one observation satisfies the canonical quality contract."""
    return observation_quality.is_accepted(
        value=observation.value,
        valid_pixels_pct=observation.valid_pixels_pct,
        cloud_cover_pct=observation.cloud_cover_pct,
        minimum_valid_pixels_pct=QUALITY_VALID_PIXELS_MIN,
    )


def _cloud_within(cloud_cover_pct: float | None, maximum: float) -> bool:
    """Whether measured cloud metadata is at or below ``maximum``.

    Unmeasured metadata cannot support a positive claim about cloud cover, so
    it does not satisfy a high-confidence criterion. It does not reject the
    observation either; that is the acceptance contract's job.
    """
    if cloud_cover_pct is None:
        return False
    return cloud_cover_pct <= maximum


def candidate_eligible(
    reference: Observation,
    candidate: Observation,
    *,
    reference_date: date,
    minimum_separation_days: int,
) -> bool:
    if not 1 <= minimum_separation_days <= 30:
        raise ValueError("minimum separation is outside the contract")
    minimum_date = reference_date + timedelta(days=minimum_separation_days)
    return (
        reference.field_id == candidate.field_id
        and reference.index_code == candidate.index_code
        and candidate.observed_at > reference.observed_at
        and candidate.observed_at > reference_date
        and candidate.observed_at >= minimum_date
        and accepted_quality(reference)
        and accepted_quality(candidate)
    )


def rounded(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def resolve_direction(
    reference: Observation | None,
    candidate: Observation | None,
    *,
    field_id: int,
    index_code: str,
    reference_date: date,
    minimum_separation_days: int,
) -> dict:
    normalized_code = index_code.strip().lower()
    if normalized_code not in SUPPORTED_INDEX_CODES:
        raise ValueError("unsupported index")
    if reference is None or candidate is None:
        return {
            "result": "insufficient_data",
            "confidence": "low",
            "algorithm_version": ALGORITHM_VERSION,
            "limitation": LIMITATION,
        }
    if reference.field_id != field_id or reference.index_code != normalized_code:
        raise ValueError("reference observation does not match request scope")
    if not candidate_eligible(
        reference,
        candidate,
        reference_date=reference_date,
        minimum_separation_days=minimum_separation_days,
    ):
        return {
            "result": "insufficient_data",
            "confidence": "low",
            "algorithm_version": ALGORITHM_VERSION,
            "limitation": LIMITATION,
        }

    reference_value = rounded(reference.value)
    observation_value = rounded(candidate.value)
    delta = observation_value - reference_value
    if delta > 0:
        result = "improved"
    elif delta < 0:
        result = "worsened"
    else:
        result = "unchanged"
    high = (
        reference.valid_pixels_pct >= HIGH_VALID_PIXELS_MIN
        and candidate.valid_pixels_pct >= HIGH_VALID_PIXELS_MIN
        and _cloud_within(reference.cloud_cover_pct, HIGH_CLOUD_MAX)
        and _cloud_within(candidate.cloud_cover_pct, HIGH_CLOUD_MAX)
    )
    return {
        "result": result,
        "confidence": "high" if high else "medium",
        "reference_value": float(reference_value),
        "observation_value": float(observation_value),
        "delta_value": float(delta),
        "algorithm_version": ALGORITHM_VERSION,
        "limitation": LIMITATION,
    }
