"""
Multi-index satellite calculation layer for Sentinel-2 vegetation/water-stress indices.

Isolated module — does NOT depend on SQLAlchemy models, API routes, or DB writes.
Does NOT modify the existing NDVI pipeline.
"""

import json
import logging
import math
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

EPSILON = 1e-10

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
    """SCL mask expression: 1 = valid (no clouds/shadow/snow/defect), 0 = reject."""
    return (
        f"![1, 3, 8, 9, 10, 11].includes({sample_var}.SCL) ? 1 : 0"
    )


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
    """Generate output band entries for the evalscript."""
    entries = []
    for code in index_codes:
        code_norm = normalize_index_code(code)
        entries.append(
            f'      {{ id: "{code_norm}", bands: 1, sampleType: "FLOAT32" }}',
        )
    entries.append('      { id: "dataMask", bands: 1 }')
    return ",\n".join(entries)


def _input_bands(index_codes: list[str]) -> list[str]:
    """Collect the union of all required bands for the requested indices."""
    bands: set[str] = set()
    for code in index_codes:
        code_norm = normalize_index_code(code)
        definition = get_index_definition(code_norm)
        if definition:
            bands.update(definition.required_bands)
    bands.add("SCL")
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
    return_lines += "    dataMask: [isValid ? 1 : 0]"

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


def _interval_is_valid_for_index(interval: dict, code: str) -> tuple[bool, str]:
    """Check whether an interval has usable stats for the given index code.

    Returns (True, "") if valid, (False, reason) if invalid.
    """
    try:
        stats = interval["outputs"][code]["bands"]["B0"]["stats"]
    except KeyError:
        return False, "missing output bands/stats"

    sample_count = safe_int(stats.get("sampleCount", 0))
    no_data_count = safe_int(stats.get("noDataCount", 0))

    if sample_count <= 0:
        return False, f"sampleCount={sample_count} <= 0"
    if no_data_count >= sample_count:
        return False, f"noDataCount={no_data_count} >= sampleCount={sample_count}"

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
    When multiple valid intervals exist, the latest (chronologically) is chosen.
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

    for code in codes:
        valid_intervals = []
        for i in intervals:
            ok, reason = _interval_is_valid_for_index(i, code)
            if ok:
                valid_intervals.append(i)

        if not valid_intervals:
            logger.info(f"No valid intervals for index '{code}'")
            continue

        latest = valid_intervals[-1]
        interval_date = latest["interval"]["to"][:10]
        stats = latest["outputs"][code]["bands"]["B0"]["stats"]
        percentiles = stats.get("percentiles", {})

        sample_count = safe_int(stats.get("sampleCount", 0))
        no_data_count = safe_int(stats.get("noDataCount", 0))

        # valid_pixels_pct = (sampleCount - noDataCount) / sampleCount * 100
        # Guaranteed sample_count > 0 and no_data_count < sample_count by validity check.
        valid_pct = ((sample_count - no_data_count) / sample_count) * 100

        # Percentile keys: Sentinel Hub may use "10.0" or "10"; same for "90.0"/"90"
        p10_val = safe_float(percentiles.get("10.0") or percentiles.get("10"))
        p90_val = safe_float(percentiles.get("90.0") or percentiles.get("90"))

        results[code] = {
            "captured_date": interval_date,
            "index_code": code,
            "mean_value": round(safe_float(stats.get("mean")), 4),
            "min_value": round(safe_float(stats.get("min")), 4),
            "max_value": round(safe_float(stats.get("max")), 4),
            "std_value": round(safe_float(stats.get("stDev")), 4),
            "p10_value": round(p10_val, 4) if p10_val is not None else None,
            "p90_value": round(p90_val, 4) if p90_val is not None else None,
            "valid_pixels_pct": round(valid_pct, 1),
            "cloud_cover_pct": None,
            "satellite": "Sentinel-2",
        }

    return results


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

    if cloud_cover_pct is not None and cloud_cover_pct > 30:
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
