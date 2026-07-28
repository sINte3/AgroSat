"""Pure deterministic next-observation eligibility and direction engine."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP


SUPPORTED_INDEX_CODES = frozenset({"ndvi", "savi", "evi", "ndmi", "ndre"})
ALGORITHM_VERSION = "observation_direction_v1"
QUALITY_VALID_PIXELS_MIN = 50.0
QUALITY_CLOUD_MAX = 30.0
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
    cloud_cover_pct: float
    satellite: str


def accepted_quality(observation: Observation) -> bool:
    return (
        0 <= observation.cloud_cover_pct <= QUALITY_CLOUD_MAX
        and QUALITY_VALID_PIXELS_MIN <= observation.valid_pixels_pct <= 100
    )


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
        and reference.cloud_cover_pct <= HIGH_CLOUD_MAX
        and candidate.cloud_cover_pct <= HIGH_CLOUD_MAX
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
