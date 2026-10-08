"""
Multi-index satellite calculation layer for Sentinel-2 vegetation/water-stress indices.

Isolated module — does NOT depend on SQLAlchemy models, API routes, or DB writes.
Does NOT modify the existing NDVI pipeline.
"""

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from services import observation_quality
from services.satellite import NDVI_INVALID_SCL_CLASSES, OBSERVED_FOOTPRINT_OUTPUT

logger = logging.getLogger(__name__)

EPSILON = 1e-10
#: The supporting indices use the NDVI mask contract: input dataMask plus the
#: same excluded SCL classes, including 0 (NO_DATA). Before TASK_246 class 0
#: and the input dataMask were ignored, so a pixel without data counted as
#: valid with zero reflectance and every index evaluated to exactly 0.0.
INVALID_SCL_CLASSES = NDVI_INVALID_SCL_CLASSES

# ─── Index Definitions ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SatelliteIndexDefinition:
    code: str
    name: str
    description: str
    formula: str
    required_bands: list[str] = field(default_factory=list)
    unit: str = "dimensionless"
    range_min: float = -1.0
    range_max: float = 1.0
    lower_is_better: bool = False


INDEX_DEFINITIONS: dict[str, SatelliteIndexDefinition] = {
    "savi": SatelliteIndexDefinition(
        code="savi",
        name="Soil-Adjusted Vegetation Index",
        description="Vegetation index corrected for soil brightness (L=0.5)",
        formula="((B08 - B04) / (B08 + B04 + 0.5)) * 1.5",
        required_bands=["B04", "B08"],
    ),
    "evi": SatelliteIndexDefinition(
        code="evi",
        name="Enhanced Vegetation Index",
        description="Vegetation index with atmospheric/soil correction",
        formula="2.5 * (B08 - B04) / (B08 + 6*B04 - 7.5*B02 + 1)",
        required_bands=["B02", "B04", "B08"],
    ),
    "ndmi": SatelliteIndexDefinition(
        code="ndmi",
        name="Normalized Difference Moisture Index (Gao NDWI for plant water stress)",
        description="Plant water stress / canopy moisture (business use case, not open-water NDWI)",
        formula="(B08 - B11) / (B08 + B11)",
        required_bands=["B08", "B11"],
    ),
    "ndre": SatelliteIndexDefinition(
        code="ndre",
        name="Normalized Difference Red Edge",
        description="Red-edge vegetation index sensitive to N/C content (B8A/B05 @ 20m)",
        formula="(B8A - B05) / (B8A + B05)",
        required_bands=["B05", "B8A"],
    ),
}

SUPPORTED_INDEX_CODES: set[str] = set(INDEX_DEFINITIONS.keys())


def normalize_index_code(index_code: str) -> str:
    """Normalize an index code: lowercase, strip whitespace."""
    return index_code.strip().lower()


def get_index_definition(index_code: str) -> Optional[SatelliteIndexDefinition]:
    """Look up an index definition by normalized code. Returns None if unknown."""
    return INDEX_DEFINITIONS.get(normalize_index_code(index_code))


# ─── Sentinel Hub Evalscript Builder ───────────────────────────────────────────

def _scl_reject_mask(sample_var: str = "sample") -> str:
    """Validity expression: data present and SCL not excluded (no data/cloud/shadow/snow/defect)."""
    excluded = json.dumps(list(INVALID_SCL_CLASSES), separators=(",", ":"))
    return f"{sample_var}.dataMask === 1 && !{excluded}.includes({sample_var}.SCL)"


def _epsilon_var_name() -> str:
    """Return a JS-safe epsilon constant name."""
    return "e"


def _build_evaluate_pixel_body(index_codes: list[str]) -> str:
    """Generate evaluatePixel body lines for the given index codes."""
    e = _epsilon_var_name()
    lines = [f"  let {e} = {EPSILON!r};"]
    lines.append("  let s = sample;")
    lines.append(f"  let isValid = {_scl_reject_mask()};")

    formula_map = {
        "savi": "((s.B08 - s.B04) / (s.B08 + s.B04 + 0.5)) * 1.5",
        "evi": "2.5 * (s.B08 - s.B04) / (s.B08 + 6*s.B04 - 7.5*s.B02 + 1 + {e})".format(e=e),
        "ndmi": "(s.B08 - s.B11) / (s.B08 + s.B11 + {e})".format(e=e),
        "ndre": "(s.B8A - s.B05) / (s.B8A + s.B05 + {e})".format(e=e),
    }

    for code in index_codes:
        code_norm = normalize_index_code(code)
        formula = formula_map.get(code_norm)
        if formula:
            lines.append(f"  let {code_norm} = {formula};")

    return "\n".join(lines)


def _output_bands(index_codes: list[str]) -> str:
    """Generate output band entries for the evalscript.

    Every index output and the observed footprint output get their own band in
    the ``dataMask`` output (Statistical API per-output masks).
    """
    entries = []
    masked = []
    for code in index_codes:
        code_norm = normalize_index_code(code)
        entries.append(
            f'      {{ id: "{code_norm}", bands: 1, sampleType: "FLOAT32" }}',
        )
        masked.append(code_norm)
    entries.append(f'      {{ id: "{OBSERVED_FOOTPRINT_OUTPUT}", bands: 1, sampleType: "UINT8" }}')
    masked.append(OBSERVED_FOOTPRINT_OUTPUT)
    entries.append(f'      {{ id: "dataMask", bands: {json.dumps(masked)} }}')
    return ",\n".join(entries)


def _input_bands(index_codes: list[str]) -> list[str]:
    """Collect the union of all required bands for the requested indices."""
    bands: set[str] = set()
    for code in index_codes:
        code_norm = normalize_index_code(code)
        definition = get_index_definition(code_norm)
        if definition:
            bands.update(definition.required_bands)
    bands.update(("SCL", "dataMask"))
    return sorted(bands)


def build_multi_index_evalscript(index_codes: list[str]) -> str:
    """
    Build a Sentinel Hub evalscript that computes multiple indices in one pass.

    Args:
        index_codes: List of index codes (e.g. ["savi", "evi", "ndmi", "ndre"]).

    Returns:
        A JavaScript evalscript string ready to send to Sentinel Hub Statistical API.
    """
    codes = [normalize_index_code(c) for c in index_codes if normalize_index_code(c) in SUPPORTED_INDEX_CODES]
    if not codes:
        raise ValueError("No supported index codes provided")

    bands_json = json.dumps(_input_bands(codes))

    return_lines = "".join(f"    {c}: [{c}],\n" for c in codes)
    return_lines += f"    {OBSERVED_FOOTPRINT_OUTPUT}: [sample.dataMask],\n"
    masks = ", ".join(["isValid ? 1 : 0"] * len(codes) + ["sample.dataMask"])
    return_lines += f"    dataMask: [{masks}]"

    script = f"""//VERSION=3

function setup() {{
  return {{
    input: [{{ bands: {bands_json} }}],
    output: [
{_output_bands(codes)}
    ]
  }};
}}

function evaluatePixel(sample) {{
{_build_evaluate_pixel_body(codes)}
  return {{
{return_lines}
  }};
}}
"""
    return script.lstrip("\n")


# ─── Response Parser ───────────────────────────────────────────────────────────

def _is_finite_stat(v) -> bool:
    """Return True if v is a finite (not NaN/Inf) number. Accepts str, float, int, None."""
    if v is None:
        return False
    try:
        val = float(v)
        return math.isfinite(val)
    except (TypeError, ValueError):
        return False


def safe_float(v, default=None):
    """Convert to float, returning default (None) for None/invalid/NaN/Inf.

    Use this for parsed satellite statistics — a real zero stays 0.0;
    NaN/Inf/None becomes None.
    """
    try:
        val = float(v)
        if math.isnan(val) or math.isinf(val):
            return default
        return val
    except (TypeError, ValueError):
        return default


def safe_int(v, default=0):
    """Convert to int, returning default for None/invalid."""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _output_stats(interval: dict, output_id: str) -> dict:
    outputs = interval.get("outputs") if isinstance(interval, dict) else None
    outputs = outputs if isinstance(outputs, dict) else {}
    stats = outputs.get(output_id, {}).get("bands", {}).get("B0", {}).get("stats")
    return stats if isinstance(stats, dict) else {}


def _observed_pixels(interval: dict) -> Optional[int]:
    """Pixels of the field polygon with data in this interval (provider counts)."""
    stats = _output_stats(interval, OBSERVED_FOOTPRINT_OUTPUT)
    return observation_quality.observed_pixel_count(stats.get("sampleCount"), stats.get("noDataCount"))


def response_field_pixels(response_data: dict, expected_field_pixels: Optional[float] = None) -> Optional[int]:
    """Field pixels of the one request grid shared by every interval of a response."""
    try:
        intervals = response_data.get("data", []) or []
    except (TypeError, AttributeError):
        intervals = []
    return observation_quality.field_pixel_count(
        [_observed_pixels(interval) for interval in intervals if isinstance(interval, dict)],
        expected_field_pixels,
    )


def _field_sample_verdict(interval: dict, code: str, field_pixels: Optional[int]) -> observation_quality.FieldSampleVerdict:
    """Provider pixel counts of one index output against the field pixels of the grid."""
    index_stats = _output_stats(interval, code)
    return observation_quality.evaluate_field_samples(
        sample_count=index_stats.get("sampleCount"),
        no_data_count=index_stats.get("noDataCount"),
        # An interval without its own observed footprint is not evaluated.
        field_pixel_count=field_pixels if _observed_pixels(interval) is not None else None,
    )


def _sample_fields(verdict: observation_quality.FieldSampleVerdict) -> dict:
    """Record fields carried from the provider counts; never defaulted."""
    return {
        "valid_pixels_pct": verdict.valid_pixels_pct,
        "sample_count": verdict.sample_count,
        "no_data_count": verdict.no_data_count,
        "valid_pixel_count": verdict.valid_sample_count,
        "field_pixel_count": verdict.field_sample_count,
        "field_samples_accepted": verdict.accepted,
        "field_samples_reason": verdict.reason,
    }


def _interval_is_valid_for_index(interval: dict, code: str) -> tuple[bool, str]:
    """Check whether an interval has usable stats for the given index code.

    Usable means at least one valid pixel by the provider's own counts and
    finite mean/min/max/stDev. Whether the valid pixels make a trustworthy field
    statistic is a separate verdict (``field_samples_accepted``) enforced on the
    write path. Returns (True, "") if usable, (False, reason) otherwise.
    """
    try:
        stats = interval["outputs"][code]["bands"]["B0"]["stats"]
    except (KeyError, TypeError):
        return False, "missing output bands/stats"

    valid = observation_quality.observed_pixel_count(stats.get("sampleCount"), stats.get("noDataCount"))
    if not valid:
        return False, "no valid samples (missing counts or every pixel masked)"

    # Check that all required numeric stats are present and finite
    for stat_key in ("mean", "min", "max", "stDev"):
        if not _is_finite_stat(stats.get(stat_key)):
            return False, f"stat '{stat_key}' missing or non-finite"

    percentiles = stats.get("percentiles", {})
    # Sentinel Hub may use "10.0" or "10" as the key
    for p_key in ("10.0", "10", "90.0", "90"):
        if p_key in percentiles:
            if not _is_finite_stat(percentiles[p_key]):
                return False, f"percentile '{p_key}' non-finite"
            break  # found a valid key for this percentile

    return True, ""


def parse_multi_index_stats_response(
    response_data: dict,
    index_codes: list[str],
    expected_field_pixels: Optional[float] = None,
) -> dict[str, dict]:
    """
    Parse a Sentinel Hub Statistical API response into per-index result dicts.

    Args:
        response_data: The full JSON response from Sentinel Hub Statistics API.
        index_codes: Expected index codes (e.g. ["savi", "evi", "ndmi", "ndre"]).

    Returns:
        Dict keyed by index code, each value a dict with captured_date,
        index_code, mean/min/max/std/p10/p90, valid_pixels_pct, etc.

    Intervals with NaN stats, full noData, or missing outputs are skipped.
    When multiple valid intervals exist, the last non-ambiguous interval is
    chosen (or the last interval overall if all are ambiguous).

    captured_date is derived from the interval metadata ``from`` timestamp
    (not from the CLI request ``date_to``), ensuring historical backfill
    correctness.
    """
    codes = [normalize_index_code(c) for c in index_codes]
    results: dict[str, dict] = {}

    try:
        intervals = response_data.get("data", [])
    except (TypeError, AttributeError):
        logger.error("parse_multi_index_stats_response: response_data is not a dict")
        return results

    if not intervals:
        logger.warning("Sentinel Hub returned empty intervals")
        return results
    field_pixels = response_field_pixels(response_data, expected_field_pixels)

    for code in codes:
        latest_entry: Optional[dict] = None

        for interval in intervals:
            ok, reason = _interval_is_valid_for_index(interval, code)
            if not ok:
                continue

            interval_from_str = interval.get("interval", {}).get("from", "")
            interval_to_str = interval.get("interval", {}).get("to", "")

            if not interval_from_str:
                logger.info("Skipping interval for '%s': missing interval.from", code)
                continue

            # ── Detect ambiguous multi-day intervals ──
            interval_is_ambiguous, ambiguous_reason = _classify_interval_ambiguity(
                interval_from_str, interval_to_str,
            )

            # Ambiguous check: if we already have a non-ambiguous entry and
            # this one is ambiguous, skip.
            if interval_is_ambiguous and latest_entry is not None and not latest_entry.get("interval_ambiguous", True):
                continue

            captured_date_str = interval_from_str[:10]

            stats = interval["outputs"][code]["bands"]["B0"]["stats"]
            percentiles = stats.get("percentiles", {})
            verdict = _field_sample_verdict(interval, code, field_pixels)
            p10_val = safe_float(percentiles.get("10.0") or percentiles.get("10"))
            p90_val = safe_float(percentiles.get("90.0") or percentiles.get("90"))

            entry = {
                "captured_date": captured_date_str,
                "index_code": code,
                "mean_value": round(safe_float(stats.get("mean")), 4),
                "min_value": round(safe_float(stats.get("min")), 4),
                "max_value": round(safe_float(stats.get("max")), 4),
                "std_value": round(safe_float(stats.get("stDev")), 4),
                "p10_value": round(p10_val, 4) if p10_val is not None else None,
                "p90_value": round(p90_val, 4) if p90_val is not None else None,
                **_sample_fields(verdict),
                "cloud_cover_pct": None,
                "satellite": "Sentinel-2",
                "interval_from": interval_from_str,
                "interval_to": interval_to_str,
                "interval_ambiguous": interval_is_ambiguous,
                "interval_ambiguous_reason": ambiguous_reason,
            }

            # Prefer later interval; prefer non-ambiguous over ambiguous.
            if latest_entry is None:
                latest_entry = entry
            elif not interval_is_ambiguous and latest_entry.get("interval_ambiguous", True):
                latest_entry = entry  # non-ambiguous replaces ambiguous
            elif interval_is_ambiguous == latest_entry.get("interval_ambiguous", True):
                # Same ambiguity class — later interval wins
                if interval_from_str > latest_entry.get("interval_from", ""):
                    latest_entry = entry

        if latest_entry is not None:
            results[code] = latest_entry

    return results


def parse_multi_index_stats_response_flat(
    response_data: dict,
    index_codes: list[str],
    expected_field_pixels: Optional[float] = None,
) -> list[dict]:
    """
    Parse Sentinel Hub Statistical API response into flat per-interval records.

    Unlike ``parse_multi_index_stats_response``, this returns ALL non-ambiguous
    intervals as individual records — one per (interval, index_code) combo.

    Args:
        response_data: Full JSON response from Sentinel Hub Statistics API.
        index_codes: Expected index codes.

    Returns:
        List of dicts, each with captured_date, index_code, mean_value, etc.
        Ambiguous multi-day intervals are excluded.
    """
    codes = [normalize_index_code(c) for c in index_codes]
    results: list[dict] = []

    try:
        intervals = response_data.get("data", [])
    except (TypeError, AttributeError):
        logger.error("parse_multi_index_stats_response_flat: response_data is not a dict")
        return results

    if not intervals:
        logger.warning("Sentinel Hub returned empty intervals")
        return results
    field_pixels = response_field_pixels(response_data, expected_field_pixels)

    for interval in intervals:
        interval_from_str = interval.get("interval", {}).get("from", "")
        interval_to_str = interval.get("interval", {}).get("to", "")

        if not interval_from_str or not interval_to_str:
            continue

        # ── Detect ambiguous multi-day intervals ──
        interval_is_ambiguous, ambiguous_reason = _classify_interval_ambiguity(
            interval_from_str, interval_to_str,
        )

        # Skip ambiguous intervals entirely
        if interval_is_ambiguous:
            continue

        derived_date = interval_from_str[:10]

        for code in codes:
            ok, _ = _interval_is_valid_for_index(interval, code)
            if not ok:
                continue

            stats = interval["outputs"][code]["bands"]["B0"]["stats"]
            percentiles = stats.get("percentiles", {})
            verdict = _field_sample_verdict(interval, code, field_pixels)
            p10_val = safe_float(percentiles.get("10.0") or percentiles.get("10"))
            p90_val = safe_float(percentiles.get("90.0") or percentiles.get("90"))

            entry = {
                "captured_date": derived_date,
                "index_code": code,
                "mean_value": round(safe_float(stats.get("mean")), 4),
                "min_value": round(safe_float(stats.get("min")), 4),
                "max_value": round(safe_float(stats.get("max")), 4),
                "std_value": round(safe_float(stats.get("stDev")), 4),
                "p10_value": round(p10_val, 4) if p10_val is not None else None,
                "p90_value": round(p90_val, 4) if p90_val is not None else None,
                **_sample_fields(verdict),
                "cloud_cover_pct": None,
                "satellite": "Sentinel-2",
                "interval_from": interval_from_str,
                "interval_to": interval_to_str,
                "interval_ambiguous": interval_is_ambiguous,
                "interval_ambiguous_reason": ambiguous_reason,
            }
            results.append(entry)

    return results


def _classify_interval_ambiguity(
    interval_from_str: str,
    interval_to_str: str,
) -> tuple[bool, Optional[str]]:
    """Determine if a Sentinel Hub interval represents an ambiguous multi-day aggregate.

    A P1D (daily) interval has ``from`` at T00:00:00Z and ``to`` at T+1T00:00:00Z,
    spanning exactly 24 hours. Spans longer than 28 hours are treated as ambiguous
    multi-day aggregates.

    Returns (is_ambiguous, reason_string_or_None).
    """
    if not interval_to_str or not interval_from_str:
        return True, "missing interval boundaries"

    try:
        dt_from = datetime.fromisoformat(interval_from_str.replace("Z", "+00:00"))
        dt_to = datetime.fromisoformat(interval_to_str.replace("Z", "+00:00"))
        span_hours = (dt_to - dt_from).total_seconds() / 3600
        if span_hours > 28:
            return True, f"multi-day span ({span_hours:.0f}h): {interval_from_str} .. {interval_to_str}"
        return False, None
    except (ValueError, TypeError):
        return True, f"unparseable interval: {interval_from_str} .. {interval_to_str}"


def extract_intervals_metadata(
    response_data: dict,
    index_codes: list[str],
) -> list[dict]:
    """Extract sanitized interval metadata from a Sentinel Hub response for audit.

    Unlike ``parse_multi_index_stats_response``, this function returns ALL
    intervals with their metadata, not just one per code.  Useful for
    ``--debug-raw-intervals`` and output-log diagnostics.

    Fields returned per interval entry:
      - interval_from, interval_to
      - interval_ambiguous, interval_ambiguous_reason
      - derived_captured_date
      - indices (list of codes present in this interval)
      - per_index: dict of index_code -> {mean, min, max, stats_fields}

    No secrets (credentials, tokens, geometry) are included.
    """
    codes = [normalize_index_code(c) for c in index_codes]

    try:
        intervals = response_data.get("data", [])
    except (TypeError, AttributeError):
        return []

    result: list[dict] = []
    seen_keys: set[str] = set()  # dedup by interval_from + interval_to

    for interval in intervals:
        interval_from_str = (interval.get("interval") or {}).get("from", "")
        interval_to_str = (interval.get("interval") or {}).get("to", "")

        if not interval_from_str or not interval_to_str:
            continue

        dedup_key = f"{interval_from_str}|{interval_to_str}"
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        interval_is_ambiguous, ambiguous_reason = _classify_interval_ambiguity(
            interval_from_str, interval_to_str,
        )
        derived_date = interval_from_str[:10]

        per_index: dict[str, dict] = {}
        indices_in_interval: list[str] = []

        for code in codes:
            ok, _ = _interval_is_valid_for_index(interval, code)
            if not ok:
                continue
            indices_in_interval.append(code)
            stats_raw = interval.get("outputs", {}).get(code, {}).get("bands", {}).get("B0", {}).get("stats", {})
            per_index[code] = {
                "mean": safe_float(stats_raw.get("mean")),
                "min": safe_float(stats_raw.get("min")),
                "max": safe_float(stats_raw.get("max")),
                "sample_count": safe_int(stats_raw.get("sampleCount"), default=None),
                "no_data_count": safe_int(stats_raw.get("noDataCount"), default=None),
            }

        if not indices_in_interval:
            continue

        entry: dict = {
            "interval_from": interval_from_str,
            "interval_to": interval_to_str,
            "derived_captured_date": derived_date,
            "interval_ambiguous": interval_is_ambiguous,
            "indices": indices_in_interval,
            "per_index": per_index,
        }
        if ambiguous_reason:
            entry["interval_ambiguous_reason"] = ambiguous_reason

        result.append(entry)

    return result


# ─── Quality Gate ──────────────────────────────────────────────────────────────

def validate_index_quality(
    index_code: str,
    mean_value,
    cloud_cover_pct=None,
    min_value=None,
    max_value=None,
    valid_pixels_pct=None,
) -> tuple[bool, str]:
    """
    Validate an index value before use.

    Returns (is_valid, reason).
    """
    code = normalize_index_code(index_code)
    definition = get_index_definition(code)
    range_min = definition.range_min if definition else -1.0
    range_max = definition.range_max if definition else 1.0

    if mean_value is None:
        return False, f"{code} mean is None"

    try:
        mean_value = float(mean_value)
    except (TypeError, ValueError):
        return False, f"{code} mean not numeric: {mean_value}"

    if math.isnan(mean_value) or math.isinf(mean_value):
        return False, f"{code} NaN/Inf"

    if mean_value < range_min or mean_value > range_max:
        return False, f"{code} value {mean_value:.4f} outside [{range_min}, {range_max}]"

    if cloud_cover_pct is not None:
        try:
            cloud_cover_pct = float(cloud_cover_pct)
        except (TypeError, ValueError):
            cloud_cover_pct = None

    # Ingest admission gate, not the accepted-observation contract; only the
    # shared cloud ceiling comes from services/observation_quality.py. A NULL
    # cloud percentage is not cloud evidence and never rejects on its own.
    if (
        cloud_cover_pct is not None
        and cloud_cover_pct > observation_quality.MAX_CLOUD_COVER_PCT
    ):
        return False, f"{code} high cloud cover ({cloud_cover_pct:.1f}%)"

    if valid_pixels_pct is not None:
        try:
            valid_pixels_pct = float(valid_pixels_pct)
        except (TypeError, ValueError):
            valid_pixels_pct = None

    if valid_pixels_pct is not None and valid_pixels_pct < 30:
        return False, f"{code} low valid pixels ({valid_pixels_pct:.1f}%)"

    # Mixed-pixel rejection (shared by all indices)
    if min_value is not None and max_value is not None:
        try:
            min_val = float(min_value)
            max_val = float(max_value)
            if min_val < -0.3 and max_val > 0.4:
                return False, f"{code} mixed pixel (min={min_val:.4f}, max={max_val:.4f})"
        except (TypeError, ValueError):
            pass

    return True, "ok"
