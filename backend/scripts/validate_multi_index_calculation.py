#!/usr/bin/env python3
"""
Standalone validation script for satellite_indices.py

Tests evalscript generation, response parsing, and quality gates.
No live API calls, no DB writes, no credentials required.
"""

import json
import math
import sys
import traceback

# Allow running from project root
sys.path.insert(0, "backend")

from services.satellite_indices import (
    SUPPORTED_INDEX_CODES,
    INDEX_DEFINITIONS,
    get_index_definition,
    normalize_index_code,
    build_multi_index_evalscript,
    _is_finite_stat,
    _interval_is_valid_for_index,
    parse_multi_index_stats_response,
    validate_index_quality,
)


def test_index_definitions():
    """All four indices are defined."""
    assert "savi" in INDEX_DEFINITIONS, "savi missing"
    assert "evi" in INDEX_DEFINITIONS, "evi missing"
    assert "ndmi" in INDEX_DEFINITIONS, "ndmi missing"
    assert "ndre" in INDEX_DEFINITIONS, "ndre missing"
    assert SUPPORTED_INDEX_CODES == {"savi", "evi", "ndmi", "ndre"}
    for code in SUPPORTED_INDEX_CODES:
        assert get_index_definition(code) is not None
    assert get_index_definition("  SAVI ") is not None
    assert get_index_definition("unknown") is None
    print("OK test_index_definitions")


def test_evalscript_all_indices():
    """Building an evalscript for all four indices produces valid JS with all bands."""
    script = build_multi_index_evalscript(["savi", "evi", "ndmi", "ndre"])
    assert "//VERSION=3" in script
    assert "let s = sample;" in script, "evaluatePixel must declare s = sample before using s.Bxx"
    assert "B02" in script, "EVI needs B02"
    assert "B04" in script, "SAVI/EVI needs B04"
    assert "B08" in script, "SAVI/EVI/NDMI needs B08"
    assert "B11" in script, "NDMI needs B11"
    assert "B05" in script, "NDRE needs B05"
    assert "B8A" in script, "NDRE needs B8A"
    assert "SCL" in script, "SCL masking required"
    assert "dataMask" in script
    assert "savi" in script
    assert "evi" in script
    assert "ndmi" in script
    assert "ndre" in script
    assert "0.0000000001" in script or "1e-10" in script, "epsilon needed for division safety"
    assert "evaluatePixel" in script
    assert "setup()" in script
    assert "FLOAT32" in script
    print("OK test_evalscript_all_indices")


def test_single_index_evalscript():
    """Building an evalscript for a single index works."""
    script = build_multi_index_evalscript(["ndmi"])
    assert "ndmi" in script
    assert "B08" in script
    assert "B11" in script
    assert "B04" not in script  # not needed for NDMI
    print("OK test_single_index_evalscript")


def test_parse_multi_index_response():
    """Parse a mocked Sentinel Hub response with mixed string/float values."""
    mock_response = {
        "data": [
            {
                "interval": {"from": "2026-06-15T00:00:00Z", "to": "2026-06-20T00:00:00Z"},
                "outputs": {
                    "savi": {
                        "bands": {
                            "B0": {
                                "stats": {
                                    "sampleCount": 1500,
                                    "noDataCount": 100,
                                    "mean": "0.4211",
                                    "min": "0.1222",
                                    "max": "0.7333",
                                    "stDev": "0.0512",
                                    "percentiles": {"10.0": "0.2211", "90.0": "0.6311"},
                                },
                            }
                        }
                    }
                },
            }
        ]
    }
    result = parse_multi_index_stats_response(mock_response, ["savi"])
    assert "savi" in result
    data = result["savi"]
    assert data["index_code"] == "savi"
    assert abs(data["mean_value"] - 0.4211) < 0.001
    assert abs(data["min_value"] - 0.1222) < 0.001
    assert abs(data["max_value"] - 0.7333) < 0.001
    assert abs(data["std_value"] - 0.0512) < 0.001
    assert abs(data["p10_value"] - 0.2211) < 0.001
    assert abs(data["p90_value"] - 0.6311) < 0.001
    assert abs(data["valid_pixels_pct"] - 93.3) < 0.1  # (1500-100)/1500*100 = 93.33...
    assert data["satellite"] == "Sentinel-2"
    assert data["captured_date"] == "2026-06-20"
    print("OK test_parse_multi_index_response")


def test_parse_multi_index_with_strings():
    """Parse response where numeric values are strings."""
    mock_response = {
        "data": [
            {
                "interval": {"from": "2026-06-15T00:00:00Z", "to": "2026-06-20T00:00:00Z"},
                "outputs": {
                    "evi": {
                        "bands": {
                            "B0": {
                                "stats": {
                                    "sampleCount": 500,
                                    "noDataCount": 50,
                                    "mean": "0.3123",
                                    "min": "0.1122",
                                    "max": "0.6123",
                                    "stDev": "0.0423",
                                    "percentiles": {"10.0": "0.1801", "90.0": "0.5201"},
                                },
                            }
                        }
                    }
                },
            }
        ]
    }
    result = parse_multi_index_stats_response(mock_response, ["evi"])
    assert "evi" in result
    assert abs(result["evi"]["mean_value"] - 0.3123) < 0.001
    print("OK test_parse_multi_index_with_strings")


def test_parse_multi_index_string_sample_count():
    """Parse response where sampleCount/noDataCount are strings (Sentinel Hub may do this)."""
    mock_response = {
        "data": [
            {
                "interval": {"from": "2026-06-15T00:00:00Z", "to": "2026-06-20T00:00:00Z"},
                "outputs": {
                    "ndmi": {
                        "bands": {
                            "B0": {
                                "stats": {
                                    "sampleCount": "1500",
                                    "noDataCount": "100",
                                    "mean": "0.3123",
                                    "min": "0.1122",
                                    "max": "0.6123",
                                    "stDev": "0.0423",
                                    "percentiles": {"10.0": "0.1801", "90.0": "0.5201"},
                                },
                            }
                        }
                    }
                },
            }
        ]
    }
    result = parse_multi_index_stats_response(mock_response, ["ndmi"])
    assert "ndmi" in result, "ndmi should parse even with string counts"
    assert abs(result["ndmi"]["valid_pixels_pct"] - 93.3) < 0.1, "valid_pixels_pct should be (1500-100)/1500*100 = 93.33..."
    print("OK test_parse_multi_index_string_sample_count")


def test_parse_no_valid_intervals():
    """Empty intervals return empty dict."""
    result = parse_multi_index_stats_response({"data": []}, ["savi"])
    assert result == {}
    print("OK test_parse_no_valid_intervals")


def test_parse_missing_intervals_key():
    """Missing 'data' key returns empty dict."""
    result = parse_multi_index_stats_response({}, ["savi"])
    assert result == {}
    print("OK test_parse_missing_intervals_key")


def test_quality_gate_edge_cases():
    """Test quality gate rejects invalid values and accepts valid ones."""

    # None rejected
    valid, reason = validate_index_quality("savi", None)
    assert not valid, "None should be rejected"
    print("OK quality_gate: None rejected")

    # NaN rejected
    valid, reason = validate_index_quality("savi", float("nan"))
    assert not valid, "NaN should be rejected"
    print("OK quality_gate: NaN rejected")

    # Inf rejected
    valid, reason = validate_index_quality("savi", float("inf"))
    assert not valid, "Inf should be rejected"
    print("OK quality_gate: Inf rejected")

    # Out of range (>1.0) rejected for normalized index
    valid, reason = validate_index_quality("savi", 1.5)
    assert not valid, ">1.0 should be rejected"
    print("OK quality_gate: >1.0 rejected")

    # Out of range (< -1.0) rejected for normalized index
    valid, reason = validate_index_quality("savi", -1.5)
    assert not valid, "< -1.0 should be rejected"
    print("OK quality_gate: <-1.0 rejected")

    # High cloud cover rejected
    valid, reason = validate_index_quality("savi", 0.5, cloud_cover_pct=50)
    assert not valid, "high cloud cover should be rejected"
    print("OK quality_gate: high cloud cover rejected")

    # Low valid pixels rejected
    valid, reason = validate_index_quality("savi", 0.5, valid_pixels_pct=20)
    assert not valid, "low valid pixels should be rejected"
    print("OK quality_gate: low valid pixels rejected")

    # Valid SAVI accepted
    valid, reason = validate_index_quality("savi", 0.42, cloud_cover_pct=10, valid_pixels_pct=85)
    assert valid, f"valid SAVI should pass, got: {reason}"
    print("OK quality_gate: valid SAVI accepted")

    # Negative NDMI accepted (water stress / bare soil)
    valid, reason = validate_index_quality("ndmi", -0.15, cloud_cover_pct=10, valid_pixels_pct=85)
    assert valid, f"negative NDMI should be valid, got: {reason}"
    print("OK quality_gate: negative NDMI accepted (plant water stress)")

    # Highly negative NDMI within [-1, 1] accepted
    valid, reason = validate_index_quality("ndmi", -0.85, cloud_cover_pct=10, valid_pixels_pct=85)
    assert valid, f"NDMI -0.85 within [-1,1] should be valid, got: {reason}"
    print("OK quality_gate: NDMI -0.85 accepted")

    # NDMI below -1 rejected
    valid, reason = validate_index_quality("ndmi", -1.5, cloud_cover_pct=10, valid_pixels_pct=85)
    assert not valid, "NDMI -1.5 should be rejected"
    print("OK quality_gate: NDMI -1.5 rejected")

    # Non-numeric mean rejected
    valid, reason = validate_index_quality("evi", "not_a_number")
    assert not valid, "non-numeric mean should be rejected"
    print("OK quality_gate: non-numeric mean rejected")


def test_normalize_index_code():
    """Case and whitespace normalization."""
    assert normalize_index_code("  SAVI ") == "savi"
    assert normalize_index_code("NDMI") == "ndmi"
    assert normalize_index_code("  EvI  ") == "evi"
    print("OK test_normalize_index_code")


def test_unknown_index_evalscript():
    """Unknown index codes are filtered out."""
    import traceback
    try:
        # Only unknown codes should raise
        build_multi_index_evalscript(["bogus"])
        # Should not reach here
        assert False, "Should have raised ValueError"
    except ValueError:
        pass

    # Mixed: known + unknown — unknown filtered
    script = build_multi_index_evalscript(["savi", "bogus"])
    assert "savi" in script
    assert "bogus" not in script
    print("OK test_unknown_index_evalscript")


# ─── NaN / interval validity tests (TASK_059C) ──────────────────────────


def test_interval_is_valid_for_index():
    """_interval_is_valid_for_index rejects NaN stats, accepts valid stats."""
    valid_interval = {
        "outputs": {
            "savi": {
                "bands": {
                    "B0": {
                        "stats": {
                            "sampleCount": 100,
                            "noDataCount": 25,
                            "mean": "0.42",
                            "min": "0.10",
                            "max": "0.75",
                            "stDev": "0.05",
                            "percentiles": {"10.0": "0.20", "90.0": "0.65"},
                        }
                    }
                }
            }
        }
    }
    ok, reason = _interval_is_valid_for_index(valid_interval, "savi")
    assert ok, f"valid interval should pass, got: {reason}"

    # Reject noDataCount >= sampleCount
    bad_nd = {
        "outputs": {
            "savi": {
                "bands": {
                    "B0": {
                        "stats": {
                            "sampleCount": 1,
                            "noDataCount": 1,
                            "mean": "NaN",
                            "min": "NaN",
                            "max": "NaN",
                            "stDev": "NaN",
                            "percentiles": {"10.0": "NaN", "90.0": "NaN"},
                        }
                    }
                }
            }
        }
    }
    ok, reason = _interval_is_valid_for_index(bad_nd, "savi")
    assert not ok, "noDataCount>=sampleCount should be rejected"
    assert "noDataCount" in reason, f"reason should mention noDataCount, got: {reason}"

    # Reject NaN mean
    nan_mean = {
        "outputs": {
            "savi": {
                "bands": {
                    "B0": {
                        "stats": {
                            "sampleCount": 100,
                            "noDataCount": 0,
                            "mean": "NaN",
                            "min": "0.1",
                            "max": "0.9",
                            "stDev": "0.05",
                            "percentiles": {"10.0": "0.2", "90.0": "0.8"},
                        }
                    }
                }
            }
        }
    }
    ok, reason = _interval_is_valid_for_index(nan_mean, "savi")
    assert not ok, "NaN mean should be rejected"
    print("OK test_interval_is_valid_for_index")


def test_test_a_nan_interval_skipped_older_valid_selected():
    """Latest NaN interval skipped, older valid interval selected."""
    mock = {
        "data": [
            {
                "interval": {"from": "2026-06-10T00:00:00Z", "to": "2026-06-15T00:00:00Z"},
                "outputs": {
                    "savi": {
                        "bands": {
                            "B0": {
                                "stats": {
                                    "sampleCount": 1200,
                                    "noDataCount": 80,
                                    "mean": "0.431",
                                    "min": "0.112",
                                    "max": "0.751",
                                    "stDev": "0.048",
                                    "percentiles": {"10.0": "0.201", "90.0": "0.661"},
                                }
                            }
                        }
                    },
                    "evi": {
                        "bands": {
                            "B0": {
                                "stats": {
                                    "sampleCount": 1200,
                                    "noDataCount": 80,
                                    "mean": "0.312",
                                    "min": "0.082",
                                    "max": "0.612",
                                    "stDev": "0.042",
                                    "percentiles": {"10.0": "0.161", "90.0": "0.532"},
                                }
                            }
                        }
                    },
                },
            },
            {
                "interval": {"from": "2026-06-15T00:00:00Z", "to": "2026-06-20T00:00:00Z"},
                "outputs": {
                    "savi": {
                        "bands": {
                            "B0": {
                                "stats": {
                                    "sampleCount": 1,
                                    "noDataCount": 1,
                                    "mean": "NaN",
                                    "min": "NaN",
                                    "max": "NaN",
                                    "stDev": "NaN",
                                    "percentiles": {"10.0": "NaN", "90.0": "NaN"},
                                }
                            }
                        }
                    },
                    "evi": {
                        "bands": {
                            "B0": {
                                "stats": {
                                    "sampleCount": 1,
                                    "noDataCount": 1,
                                    "mean": "NaN",
                                    "min": "NaN",
                                    "max": "NaN",
                                    "stDev": "NaN",
                                    "percentiles": {"10.0": "NaN", "90.0": "NaN"},
                                }
                            }
                        }
                    },
                },
            },
        ]
    }
    result = parse_multi_index_stats_response(mock, ["savi", "evi"])
    assert "savi" in result, "savi should be returned from older interval"
    assert "evi" in result, "evi should be returned from older interval"
    # Values should be from the OLDER (valid) interval
    assert abs(result["savi"]["mean_value"] - 0.431) < 0.001, "savi mean should be non-zero from older interval"
    assert abs(result["evi"]["mean_value"] - 0.312) < 0.001, "evi mean should be non-zero from older interval"
    assert result["savi"]["captured_date"] == "2026-06-15", "should use older valid interval date"
    assert result["evi"]["captured_date"] == "2026-06-15", "should use older valid interval date"
    # Verify zero was NOT produced from NaN
    assert result["savi"]["mean_value"] != 0.0, "NaN should not become 0.0"
    assert result["evi"]["mean_value"] != 0.0, "NaN should not become 0.0"
    # Verify valid_pixels_pct
    assert abs(result["savi"]["valid_pixels_pct"] - 93.3) < 0.1
    print("OK test_test_a_nan_interval_skipped_older_valid_selected")


def test_test_b_all_intervals_nan_returns_no_fake_record():
    """All intervals NaN for an index — no fake record returned."""
    mock = {
        "data": [
            {
                "interval": {"from": "2026-06-10T00:00:00Z", "to": "2026-06-15T00:00:00Z"},
                "outputs": {
                    "savi": {
                        "bands": {
                            "B0": {
                                "stats": {
                                    "sampleCount": 1,
                                    "noDataCount": 1,
                                    "mean": "NaN",
                                    "min": "NaN",
                                    "max": "NaN",
                                    "stDev": "NaN",
                                    "percentiles": {"10.0": "NaN", "90.0": "NaN"},
                                }
                            }
                        }
                    },
                },
            },
            {
                "interval": {"from": "2026-06-15T00:00:00Z", "to": "2026-06-20T00:00:00Z"},
                "outputs": {
                    "savi": {
                        "bands": {
                            "B0": {
                                "stats": {
                                    "sampleCount": 1,
                                    "noDataCount": 1,
                                    "mean": "NaN",
                                    "min": "NaN",
                                    "max": "NaN",
                                    "stDev": "NaN",
                                    "percentiles": {"10.0": "NaN", "90.0": "NaN"},
                                }
                            }
                        }
                    },
                },
            },
        ]
    }
    result = parse_multi_index_stats_response(mock, ["savi"])
    assert "savi" not in result, "NaN-only index should not be returned"
    assert len(result) == 0, "result dict should be empty for NaN-only data"
    print("OK test_test_b_all_intervals_nan_returns_no_fake_record")


def test_test_c_valid_zero_interval_accepted():
    """Real zero values accepted, not confused with NaN."""
    mock = {
        "data": [
            {
                "interval": {"from": "2026-06-10T00:00:00Z", "to": "2026-06-15T00:00:00Z"},
                "outputs": {
                    "ndmi": {
                        "bands": {
                            "B0": {
                                "stats": {
                                    "sampleCount": 500,
                                    "noDataCount": 0,
                                    "mean": "0.0",
                                    "min": "0.0",
                                    "max": "0.0",
                                    "stDev": "0.0",
                                    "percentiles": {"10.0": "0.0", "90.0": "0.0"},
                                }
                            }
                        }
                    },
                },
            }
        ]
    }
    result = parse_multi_index_stats_response(mock, ["ndmi"])
    assert "ndmi" in result, "ndmi with real zeros should be returned"
    assert result["ndmi"]["mean_value"] == 0.0, "real zero should stay 0.0"
    assert result["ndmi"]["min_value"] == 0.0
    assert result["ndmi"]["max_value"] == 0.0
    assert result["ndmi"]["std_value"] == 0.0
    assert result["ndmi"]["p10_value"] == 0.0
    assert result["ndmi"]["p90_value"] == 0.0
    assert result["ndmi"]["valid_pixels_pct"] == 100.0, "100% valid pixels"
    print("OK test_test_c_valid_zero_interval_accepted")


def test_test_d_valid_pixels_pct_formula():
    """valid_pixels_pct uses (sampleCount - noDataCount) / sampleCount * 100."""
    mock = {
        "data": [
            {
                "interval": {"from": "2026-06-10T00:00:00Z", "to": "2026-06-15T00:00:00Z"},
                "outputs": {
                    "ndre": {
                        "bands": {
                            "B0": {
                                "stats": {
                                    "sampleCount": 100,
                                    "noDataCount": 25,
                                    "mean": "0.35",
                                    "min": "0.10",
                                    "max": "0.60",
                                    "stDev": "0.04",
                                    "percentiles": {"10.0": "0.18", "90.0": "0.52"},
                                }
                            }
                        }
                    },
                },
            }
        ]
    }
    result = parse_multi_index_stats_response(mock, ["ndre"])
    assert "ndre" in result, "ndre should be returned"
    assert result["ndre"]["valid_pixels_pct"] == 75.0, \
        f"expected 75.0, got {result['ndre']['valid_pixels_pct']}"
    print("OK test_test_d_valid_pixels_pct_formula")


def test_is_finite_stat():
    """_is_finite_stat correctly distinguishes valid from non-finite."""
    assert _is_finite_stat(0.0) is True
    assert _is_finite_stat("0.0") is True
    assert _is_finite_stat(-1.5) is True
    assert _is_finite_stat(float("nan")) is False
    assert _is_finite_stat(float("inf")) is False
    assert _is_finite_stat("NaN") is False
    assert _is_finite_stat("Infinity") is False
    assert _is_finite_stat(None) is False
    assert _is_finite_stat("garbage") is False
    print("OK test_is_finite_stat")


def main():
    tests = [
        test_index_definitions,
        test_evalscript_all_indices,
        test_single_index_evalscript,
        test_parse_multi_index_response,
        test_parse_multi_index_with_strings,
        test_parse_multi_index_string_sample_count,
        test_parse_no_valid_intervals,
        test_parse_missing_intervals_key,
        test_quality_gate_edge_cases,
        test_normalize_index_code,
        test_unknown_index_evalscript,
        # NaN / interval validity tests (TASK_059C)
        test_is_finite_stat,
        test_interval_is_valid_for_index,
        test_test_a_nan_interval_skipped_older_valid_selected,
        test_test_b_all_intervals_nan_returns_no_fake_record,
        test_test_c_valid_zero_interval_accepted,
        test_test_d_valid_pixels_pct_formula,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except Exception as e:
            print(f"FAIL {test.__name__}: {e}")
            traceback.print_exc()
            failures += 1

    print()
    if failures:
        print(f"FAILURES: {failures}/{len(tests)} tests failed")
        sys.exit(1)
    else:
        print(f"ALL {len(tests)} TESTS PASSED")


if __name__ == "__main__":
    main()
