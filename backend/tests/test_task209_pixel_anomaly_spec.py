from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "docs" / "TASK_209_PIXEL_ANOMALY_CONTRACT.md"


def source():
    return " ".join(SPEC.read_text(encoding="utf-8").split())


def test_spec_defines_non_diagnostic_pixel_contract():
    text = source()
    assert "inspection candidates, not agronomic diagnoses" in text
    assert "No mock or colorized PNG may be interpreted as numeric pixel values" in text
    assert "paired_within_field_drop_v1" in text
    assert "current <= current_median - within_field_delta" in text
    assert "paired_delta <= -comparison_drop" in text
    assert "Four-way orthogonal connectivity" in text


def test_spec_defines_quality_temporal_and_insufficient_data_gates():
    text = source()
    for contract in (
        "cloud cover at or below 30%",
        "within-field valid pixels at or above 60%",
        "at least 25 eligible field pixels",
        "at least 5 days and at most 60 days",
        "`insufficient_data`",
    ):
        assert contract in text


def test_spec_defines_persistence_and_storage_contract():
    text = source()
    for contract in (
        "`single_scene`",
        "`persistent`",
        "`recovering`",
        "`pixel_anomaly_runs`",
        "`pixel_anomalies`",
        "`pixel_anomaly_inspections`",
        "No raster pixel arrays are stored in PostgreSQL",
    ):
        assert contract in text


def test_spec_defines_bounded_cli_api_and_tenant_contract():
    text = source()
    assert "standalone CLI" in text
    assert "Fixture input is rejected with `--write`" in text
    assert "bounded field batch (`1..100`)" in text
    assert "GET /api/pixel-anomalies/fields/{field_id}/summary" in text
    assert "POST /api/pixel-anomalies/{anomaly_id}/inspection" in text
    assert "Cross-tenant objects return non-enumerable 404" in text
    assert "Inspection creation denies `viewer`" in text


def test_spec_records_all_external_validation_boundaries():
    text = source()
    for blocker in ("B-001", "B-003", "B-004"):
        assert blocker in text
