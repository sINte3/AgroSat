from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "docs" / "TASK_209_VECTOR_RASTER_ARCHITECTURE.md"


def test_vector_raster_spec_is_current_and_bounded():
    text = SPEC.read_text(encoding="utf-8")
    for required in (
        "GET /api/field-tiles/metadata",
        "GET /api/field-tiles/{z}/{x}/{y}.mvt",
        "task209_field_mvt_v1",
        "MVT extent: 4096",
        "MVT buffer: 64",
        "idx_fields_geometry",
        "GET /api/raster/fields/{field_id}/metadata",
        "GET /api/raster/fields/{field_id}/image",
        "sentinel_process",
        "Unsupported indices return an explicit 422",
        "Missing credentials return a closed 503",
    ):
        assert required in text


def test_vector_raster_spec_preserves_security_and_compatibility():
    text = SPEC.read_text(encoding="utf-8")
    for required in (
        "limited to their assigned enterprise",
        "cross-enterprise request is rejected",
        "Tile cache keys include schema version, effective tenant scope",
        "Provider credentials, Sentinel evalscripts, and provider selection never enter frontend code",
        "A mock provider is never selected in production paths",
        "Existing `/api/ndvi-raster/*` endpoints remain operational",
        "Camera movement must not fetch the full dataset",
        "cannot render",
    ):
        assert required in text
