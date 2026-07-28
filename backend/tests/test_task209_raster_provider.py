from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

from api import raster as raster_api
from main import app
from schemas.raster import RasterMetadataResponse
from services import raster_provider


PNG = b"\x89PNG\r\n\x1a\nprovider-fixture"


def row(*, exact=False):
    values = {
        "field_id": 16,
        "enterprise_id": 7,
        "observation_id": 91,
        "observation_date": date(2026, 7, 14),
        "satellite": "Sentinel-2",
        "west": 64.1,
        "south": 39.5,
        "east": 64.2,
        "north": 39.6,
    }
    if exact:
        values["geometry"] = {
            "type": "Polygon",
            "coordinates": [],
        }
    return SimpleNamespace(**values)


def user(role="viewer", enterprise_id=7):
    return SimpleNamespace(role=role, enterprise_id=enterprise_id)


def test_registry_supports_ndvi_and_rejects_unsupported_index():
    provider = raster_provider.get_provider("NDVI")
    assert provider.code == "sentinel_process"
    assert provider.supported_indices == ("ndvi",)
    metadata = raster_provider.provider_metadata("ndvi")
    assert metadata["provider"] == "sentinel_process"
    assert metadata["quality_mask"]
    assert "credential" not in metadata
    with pytest.raises(raster_provider.UnsupportedRasterIndex):
        raster_provider.get_provider("savi")


@patch.object(
    raster_provider.ndvi_raster,
    "get_raster_png",
    return_value=(PNG, "MISS"),
)
def test_provider_delegates_to_real_fail_closed_ndvi_implementation(render):
    image, state, metadata = raster_provider.render_raster(
        16,
        {"type": "Polygon", "coordinates": []},
        "ndvi",
        date(2026, 7, 14),
        512,
    )
    assert (image, state) == (PNG, "MISS")
    assert metadata["processing_version"] == raster_provider.PROCESSING_VERSION
    render.assert_called_once()


def test_generic_metadata_is_tenant_scoped_and_provider_neutral():
    db = Mock()
    db.execute.return_value.fetchone.return_value = row()
    result = raster_api.get_raster_metadata(
        field_id=16,
        index_code="ndvi",
        date_to=date(2026, 7, 14),
        db=db,
        current_user=user(),
    )
    validated = RasterMetadataResponse.model_validate(result)
    assert validated.schema_version == "task209_raster_provider_v1"
    assert validated.index_code == "ndvi"
    assert validated.provenance.provider == "sentinel_process"
    assert validated.quality.accepted_observation is True
    assert "index_code=ndvi" in validated.image_template
    sql = str(db.execute.call_args.args[0])
    assert "f.enterprise_id = :enterprise_id" in sql
    assert "n.captured_date <= :date_to" in sql
    assert "n.mean_ndvi IS NOT NULL" in sql
    assert "n.satellite = 'Sentinel-2'" in sql
    db.commit.assert_not_called()


def test_unsupported_index_fails_before_database_access():
    db = Mock()
    with pytest.raises(HTTPException) as caught:
        raster_api.get_raster_metadata(
            field_id=16,
            index_code="savi",
            date_to=date(2026, 7, 14),
            db=db,
            current_user=user(),
        )
    assert caught.value.status_code == 422
    assert caught.value.detail == "Unsupported raster index"
    db.execute.assert_not_called()


@patch.object(
    raster_api,
    "render_raster",
    return_value=(PNG, "BYPASS", {"provider": "sentinel_process"}),
)
def test_generic_image_uses_exact_observation_and_safe_headers(render):
    db = Mock()
    db.execute.return_value.fetchone.return_value = row(exact=True)
    response = raster_api.get_raster_image(
        field_id=16,
        observation_date=date(2026, 7, 14),
        index_code="ndvi",
        size=512,
        db=db,
        current_user=user(),
    )
    assert response.media_type == "image/png"
    assert response.headers["x-agrosat-raster-provider"] == "sentinel_process"
    assert response.headers["x-agrosat-raster-index"] == "ndvi"
    assert response.headers["cache-control"] == "private, max-age=86400"
    sql = str(db.execute.call_args.args[0])
    assert "n.captured_date = :observation_date" in sql
    render.assert_called_once()


@pytest.mark.parametrize(
    ("failure", "status"),
    (
        (raster_provider.RasterServiceUnavailable("sensitive"), 503),
        (raster_provider.RasterUpstreamTimeout("sensitive"), 504),
        (raster_provider.RasterUpstreamInvalid("sensitive"), 502),
    ),
)
def test_generic_image_errors_are_classified_and_sanitized(failure, status):
    db = Mock()
    db.execute.return_value.fetchone.return_value = row(exact=True)
    with patch.object(raster_api, "render_raster", side_effect=failure):
        with pytest.raises(HTTPException) as caught:
            raster_api.get_raster_image(
                field_id=16,
                observation_date=date(2026, 7, 14),
                index_code="ndvi",
                size=512,
                db=db,
                current_user=user(),
            )
    assert caught.value.status_code == status
    assert "sensitive" not in caught.value.detail


def test_generic_and_legacy_routes_are_registered():
    paths = app.openapi()["paths"]
    for path in (
        "/api/raster/fields/{field_id}/metadata",
        "/api/raster/fields/{field_id}/image",
        "/api/ndvi-raster/fields/{field_id}/metadata",
        "/api/ndvi-raster/fields/{field_id}/image",
    ):
        assert paths[path]["get"]["security"] == [{"OAuth2PasswordBearer": []}]
