"""Focused offline qualification for TASK_215 Pixel NDVI Workspace."""

from dataclasses import replace
from datetime import date, datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from fastapi import HTTPException
from rasterio.crs import CRS
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds

from api import raster as raster_api
from services import pixel_ndvi


GEOMETRY = {
    "type": "Polygon",
    "coordinates": [[[64.0, 39.0], [64.1, 39.0], [64.1, 39.1], [64.0, 39.1], [64.0, 39.0]]],
}


def scene(*, enterprise_id=7, field_id=4, provider_scene_id="S2A_TEST_SCENE", observation_date=date(2026, 8, 10)):
    return pixel_ndvi.SceneRow(
        field_id=field_id,
        enterprise_id=enterprise_id,
        provider_scene_id=provider_scene_id,
        acquisition_time=datetime.combine(observation_date, datetime.min.time(), timezone.utc),
        cloud_cover_pct=12.5,
        valid_pixels_pct=82.0,
        satellite="Sentinel-2",
        geometry=GEOMETRY,
        bbox=(64.0, 39.0, 64.1, 39.1),
    )


@pytest.fixture(autouse=True)
def signing_secret(monkeypatch):
    monkeypatch.setattr(pixel_ndvi.settings, "secret_key", "task215-offline-signing-secret-32chars")


def test_ndvi_math_zero_nan_inf_and_cloud_shadow_snow_masks():
    red = np.array([[1.0, 0.0, np.nan, 1.0], [1.0, 1.0, 1.0, 1.0]], dtype=np.float32)
    nir = np.array([[3.0, 0.0, 1.0, np.inf], [3.0, 3.0, 3.0, 3.0]], dtype=np.float32)
    scl = np.array([[4, 4, 4, 4], [3, 8, 11, 4]], dtype=np.uint8)
    data_mask = np.ones_like(scl)
    inside = np.ones_like(scl, dtype=bool)
    inside[1, 3] = False
    values, valid = pixel_ndvi.compute_ndvi(red, nir, scl, data_mask, inside)
    assert values[0, 0] == pytest.approx(0.5)
    assert valid.sum() == 1
    assert np.isnan(values[0, 1:]).all()
    assert np.isnan(values[1]).all()
    assert {0, 1, 3, 8, 9, 10, 11}.issubset(set(pixel_ndvi.NDVI_INVALID_SCL_CLASSES))


@pytest.mark.parametrize(
    ("value", "label"),
    [
        (-0.01, "Вода, тень или поверхность без растительности"),
        (0.0, "Почва или очень слабая растительность"),
        (0.2, "Слабая растительность"),
        (0.4, "Умеренная растительность"),
        (0.6, "Сильная растительность"),
        (0.8, "Очень густая растительность"),
        (1.0, "Очень густая растительность"),
    ],
)
def test_palette_boundaries(value, label):
    assert pixel_ndvi.classify_ndvi(value)["label"] == label


def test_histogram_and_statistics_use_same_masked_values():
    ndvi = np.array([[-0.2, 0.1, 0.3], [0.5, 0.7, 0.9]], dtype=np.float32)
    valid = np.array([[True, True, True], [True, True, False]])
    result = pixel_ndvi.summarize_ndvi(ndvi, valid)
    assert result["valid_pixel_count"] == 5
    assert result["no_data_pixel_count"] == 1
    assert result["median"] == pytest.approx(0.3)
    assert sum(item["count"] for item in result["histogram"]) == 5
    assert [item["count"] for item in result["histogram"]] == [1, 1, 1, 1, 1, 0]


def test_palette_png_is_transparent_outside_field_and_bounded():
    ndvi = np.array([[0.1, 0.7], [0.3, 0.9]], dtype=np.float32)
    valid = np.array([[True, False], [True, True]])
    payload = pixel_ndvi.render_palette_png(
        ndvi, valid, from_bounds(64, 39, 64.1, 39.1, 2, 2), CRS.from_wkt(pixel_ndvi.WGS84_WKT)
    )
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(payload) <= pixel_ndvi.MAX_RESPONSE_BYTES
    with pixel_ndvi.MemoryFile(payload) as memory:
        with memory.open() as dataset:
            alpha = dataset.read(4)
    assert alpha.tolist() == [[255, 0], [255, 255]]


def test_process_payload_is_exact_cdse_l2a_10m_equivalent_and_bounded():
    row = scene()
    width, height, resolution = pixel_ndvi._dimensions(row.bbox, 10)
    payload = pixel_ndvi.build_encoded_process_payload(row, width, height)
    assert width <= pixel_ndvi.MAX_DIMENSION and height <= pixel_ndvi.MAX_DIMENSION
    assert width * height <= pixel_ndvi.MAX_PIXEL_COUNT
    assert resolution >= 10
    assert payload["input"]["bounds"]["properties"]["crs"] == "http://www.opengis.net/def/crs/EPSG/0/4326"
    assert payload["input"]["data"][0]["type"] == "sentinel-2-l2a"
    assert payload["output"]["responses"][0]["format"]["type"] == "image/png"
    assert "B08" in payload["evalscript"] and "B04" in payload["evalscript"]
    assert "SCL" in payload["evalscript"] and "dataMask" in payload["evalscript"]


def test_encoded_png_epsg4326_bounds_and_local_polygon_clip():
    row = scene()
    transform = from_bounds(*row.bbox, 4, 4)
    encoded = int(round((0.55 + 1.0) * 32767.5))
    bands = np.stack([
        np.full((4, 4), encoded // 256, dtype=np.uint8),
        np.full((4, 4), encoded % 256, dtype=np.uint8),
        np.zeros((4, 4), dtype=np.uint8),
        np.full((4, 4), 255, dtype=np.uint8),
    ])
    with MemoryFile() as memory:
        with memory.open(
            driver="PNG", width=4, height=4, count=4, dtype="uint8",
        ) as dataset:
            dataset.write(bands)
        payload = memory.read()
    ndvi, valid, decoded_transform, decoded_crs = pixel_ndvi._decode_and_clip(payload, row, 4, 4)
    assert decoded_transform == transform
    assert decoded_crs.is_geographic
    assert valid.all()
    assert np.allclose(ndvi, 0.55, atol=0.00004)


def test_cache_key_isolates_tenant_field_geometry_scene_mask_palette_and_resolution():
    base = pixel_ndvi.artifact_cache_key(scene())
    variants = [
        pixel_ndvi.artifact_cache_key(scene(enterprise_id=8)),
        pixel_ndvi.artifact_cache_key(scene(field_id=5)),
        pixel_ndvi.artifact_cache_key(scene(provider_scene_id="S2B_TEST_SCENE")),
        pixel_ndvi.artifact_cache_key(scene(observation_date=date(2026, 8, 11))),
        pixel_ndvi.artifact_cache_key(scene(), resolution=20),
    ]
    assert pixel_ndvi.KEY_PATTERN.fullmatch(base)
    assert all(value != base for value in variants)
    changed = replace(
        scene(),
        geometry={**GEOMETRY, "coordinates": [[[64, 39], [64.2, 39], [64.2, 39.1], [64, 39]]]},
    )
    assert pixel_ndvi.artifact_cache_key(changed) != base


def _artifact(row):
    ndvi = np.array([[0.2, np.nan], [0.6, 0.8]], dtype=np.float32)
    valid = np.isfinite(ndvi)
    png = pixel_ndvi.render_palette_png(
        ndvi, valid, from_bounds(*row.bbox, 2, 2), CRS.from_wkt(pixel_ndvi.WGS84_WKT)
    )
    metadata = {
        "schema_version": pixel_ndvi.SCHEMA_VERSION,
        "field_id": row.field_id,
        "enterprise_id": row.enterprise_id,
        "scene_id": pixel_ndvi.scene_id_for(row),
        "bounds": list(row.bbox),
        "corners": [[64, 39.1], [64.1, 39.1], [64.1, 39], [64, 39]],
        "width": 2,
        "height": 2,
        "source_resolution_m": 10,
        "effective_resolution_m": 10.0,
        "response_bytes": len(png),
        "summary": pixel_ndvi.summarize_ndvi(ndvi, valid),
    }
    return pixel_ndvi.PixelArtifact(png, ndvi, valid, metadata, "MISS")


def test_atomic_cache_manifest_sha_and_corrupt_entry_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(pixel_ndvi.settings, "pixel_ndvi_cache_directory", str(tmp_path))
    monkeypatch.setattr(pixel_ndvi.settings, "pixel_ndvi_cache_max_entries", 20)
    row = scene()
    key = pixel_ndvi.artifact_cache_key(row)
    artifact = _artifact(row)
    pixel_ndvi._store_cache(tmp_path.resolve(), key, artifact)
    loaded = pixel_ndvi._load_cache(tmp_path.resolve(), key)
    assert loaded is not None and loaded.cache_state == "HIT"
    png_path, array_path, manifest_path = pixel_ndvi._cache_paths(tmp_path.resolve(), key)
    manifest = json.loads(manifest_path.read_text("utf-8"))
    assert manifest["png_sha256"] == pixel_ndvi._sha256(png_path.read_bytes())
    assert manifest["array_sha256"] == pixel_ndvi._sha256(array_path.read_bytes())
    assert not list(tmp_path.rglob("*.tmp"))
    array_path.write_bytes(b"corrupt")
    assert pixel_ndvi._load_cache(tmp_path.resolve(), key) is None


def test_scene_id_tamper_and_cross_tenant_identity_are_distinct():
    token = pixel_ndvi.scene_id_for(scene())
    assert 80 <= len(token) <= 768
    assert pixel_ndvi.scene_id_for(scene(enterprise_id=8)) != token
    assert pixel_ndvi.scene_id_for(scene(field_id=5)) != token
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(HTTPException) as error:
        pixel_ndvi._decode_scene_id(tampered)
    assert error.value.status_code == 404


def test_scene_catalog_uses_provider_acquisition_time_and_tenant_scoped_geometry(monkeypatch):
    db = Mock()
    raw = SimpleNamespace(
        field_id=4, enterprise_id=7, geometry=GEOMETRY,
        west=64, south=39, east=64.1, north=39.1,
    )
    db.execute.return_value.fetchone.return_value = raw
    response = Mock(
        status_code=200,
        content=b'{"type":"FeatureCollection"}',
        headers={"content-type": "application/geo+json"},
    )
    response.json.return_value = {
        "type": "FeatureCollection",
        "features": [{
            "id": "S2A_REAL_ACQUISITION",
            "properties": {"datetime": "2026-08-10T05:12:01Z", "eo:cloud_cover": 12.5},
        }],
    }
    service = Mock(is_mock=False)
    service.get_catalog_response.return_value = response
    monkeypatch.setattr(pixel_ndvi, "get_satellite_service", lambda: service)
    monkeypatch.setattr(pixel_ndvi.settings, "sentinel_hub_provider", "cdse")
    rows = pixel_ndvi.list_scene_rows(
        db, 4, SimpleNamespace(role="agronomist", enterprise_id=7),
        date(2026, 8, 1), date(2026, 8, 20), 20,
    )
    assert rows[0].enterprise_id == 7
    assert rows[0].provider_scene_id == "S2A_REAL_ACQUISITION"
    assert rows[0].acquisition_time.isoformat() == "2026-08-10T05:12:01+00:00"
    assert service.get_catalog_response.call_args.args[0]["collections"] == ["sentinel-2-l2a"]
    sql = str(db.execute.call_args.args[0])
    assert "f.enterprise_id = :enterprise_id" in sql
    assert "ndvi_records" not in sql
    with pytest.raises(HTTPException) as error:
        pixel_ndvi.list_scene_rows(
            db, 4, SimpleNamespace(role="viewer", enterprise_id=7),
            date(2025, 1, 1), date(2026, 8, 20), 20,
        )
    assert error.value.status_code == 422


def test_point_outside_rejected_before_artifact_and_inside_sample():
    row = scene()
    pixel_ndvi.validate_point(row, 64.05, 39.05)
    with pytest.raises(HTTPException) as outside:
        pixel_ndvi.validate_point(row, 65, 40)
    assert outside.value.status_code == 422
    artifact = _artifact(row)
    value, classification = pixel_ndvi.sample_artifact(artifact, 64.025, 39.075)
    assert value == pytest.approx(0.2)
    assert classification["label"] == "Слабая растительность"
    value, classification = pixel_ndvi.sample_artifact(artifact, 64.075, 39.075)
    assert value is None and classification is None


def test_etag_conditional_response_avoids_body(monkeypatch):
    row = scene()
    artifact = _artifact(row)
    monkeypatch.setattr(raster_api, "resolve_scene_row", lambda *_: row)
    monkeypatch.setattr(raster_api, "get_pixel_artifact", lambda *_: artifact)
    response = raster_api.get_pixel_ndvi_image(
        4, pixel_ndvi.scene_id_for(row), None, Mock(), SimpleNamespace(role="viewer", enterprise_id=7)
    )
    assert response.status_code == 200
    etag = response.headers["etag"]
    conditional = raster_api.get_pixel_ndvi_image(
        4, pixel_ndvi.scene_id_for(row), etag, Mock(), SimpleNamespace(role="viewer", enterprise_id=7)
    )
    assert conditional.status_code == 304 and conditional.body == b""
    assert conditional.headers["cache-control"].startswith("private")
    assert conditional.headers["vary"] == "Authorization"


@pytest.mark.parametrize(
    ("raised", "status"),
    [
        (pixel_ndvi.RasterServiceUnavailable("sensitive"), 503),
        (pixel_ndvi.RasterUpstreamTimeout("sensitive"), 504),
        (pixel_ndvi.RasterUpstreamInvalid("sensitive"), 502),
    ],
)
def test_provider_error_normalization_contains_no_upstream_detail(raised, status):
    with pytest.raises(HTTPException) as caught:
        raster_api._raise_pixel_provider_error(raised)
    assert caught.value.status_code == status
    assert "sensitive" not in caught.value.detail


def test_real_provider_path_has_no_mock_fallback_and_fixed_cdse_guard(monkeypatch):
    monkeypatch.setattr(pixel_ndvi.settings, "sentinel_hub_provider", "planet")
    with pytest.raises(pixel_ndvi.RasterServiceUnavailable):
        pixel_ndvi._request_encoded_png(scene(), 32, 32)
    source = Path(pixel_ndvi.__file__).read_text("utf-8")
    assert "allow_mock=True" not in source
    assert "random" not in source
    assert "resolve_sentinel_provider" not in source


def test_routes_are_authenticated_and_registered():
    from main import app

    paths = app.openapi()["paths"]
    for path in (
        "/api/raster/fields/{field_id}/scenes",
        "/api/raster/fields/{field_id}/workspace",
        "/api/raster/fields/{field_id}/pixel-image",
        "/api/raster/fields/{field_id}/sample",
    ):
        assert paths[path]["get"]["security"] == [{"OAuth2PasswordBearer": []}]
