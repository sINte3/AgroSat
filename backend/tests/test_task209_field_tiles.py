from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api import field_tiles as field_tiles_api
from api import fields as fields_api
from main import app
from schemas.field_tiles import FieldTileMetadataResponse
from services import field_tiles


class MappingResult:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row


class ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class RecordingDB:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def execute(self, statement, params):
        self.calls.append((str(statement), dict(params)))
        return self.result


def actor(role="manager", enterprise_id=5):
    return SimpleNamespace(role=role, enterprise_id=enterprise_id)


def test_scope_enforces_tenant_and_known_roles():
    assert field_tiles.resolve_scope(actor()).enterprise_id == 5
    assert field_tiles.resolve_scope(actor("admin", None)).enterprise_id is None
    assert field_tiles.resolve_scope(actor("admin", None), 9).enterprise_id == 9
    assert field_tiles.resolve_scope(actor("viewer"), 5).enterprise_id == 5
    with pytest.raises(HTTPException) as cross_tenant:
        field_tiles.resolve_scope(actor(), 6)
    assert cross_tenant.value.status_code == 403
    for denied in (actor("unknown"), actor("manager", None)):
        with pytest.raises(HTTPException) as caught:
            field_tiles.resolve_scope(denied)
        assert caught.value.status_code == 403


def test_coordinates_and_simplification_are_bounded():
    assert field_tiles.simplification_tolerance_m(3) == 150
    assert field_tiles.simplification_tolerance_m(8) == 40
    assert field_tiles.simplification_tolerance_m(11) == 10
    assert field_tiles.simplification_tolerance_m(14) == 0.5
    field_tiles.validate_coordinates(3, 0, 7)
    for coordinates in ((2, 0, 0), (19, 0, 0), (3, -1, 0), (3, 8, 0)):
        with pytest.raises(HTTPException) as caught:
            field_tiles.validate_coordinates(*coordinates)
        assert caught.value.status_code == 422


def test_cache_keys_are_tenant_safe():
    global_scope = field_tiles.resolve_scope(actor("admin", None))
    tenant_scope = field_tiles.resolve_scope(actor())
    assert field_tiles.tile_cache_key(global_scope, 8, 10, 11) != (
        field_tiles.tile_cache_key(tenant_scope, 8, 10, 11)
    )
    assert "enterprise:5" in field_tiles.tile_cache_key(
        tenant_scope,
        8,
        10,
        11,
    )


@patch.object(field_tiles, "cache_set", return_value=False)
@patch.object(field_tiles, "cache_get", return_value=None)
def test_metadata_is_one_tenant_scoped_statement(_cache_get, _cache_set):
    db = RecordingDB(
        MappingResult(
            {
                "field_count": 2,
                "bounds": [63.1, 39.1, 64.1, 40.1],
            }
        )
    )
    scope = field_tiles.resolve_scope(actor())
    payload = field_tiles.get_metadata(db, scope)
    assert len(db.calls) == 1
    sql, params = db.calls[0]
    assert "f.enterprise_id = :enterprise_id" in sql
    assert "ST_Extent(geometry)" in sql
    assert params == {"enterprise_id": 5}
    assert payload["tile_template"].endswith("?enterprise_id=5")
    assert payload["field_count"] == 2
    FieldTileMetadataResponse.model_validate(payload)


@patch.object(field_tiles, "cache_set_binary", return_value=True)
@patch.object(field_tiles, "cache_get_binary", return_value=None)
def test_tile_is_one_index_compatible_statement(_cache_get, _cache_set):
    db = RecordingDB(ScalarResult(b"fixture-mvt"))
    scope = field_tiles.resolve_scope(actor())
    tile, cache_state, etag = field_tiles.get_tile(db, scope, 8, 10, 11)
    assert tile == b"fixture-mvt"
    assert cache_state == "MISS"
    assert etag.startswith('"') and etag.endswith('"')
    assert len(db.calls) == 1
    sql, params = db.calls[0]
    for required in (
        "ST_TileEnvelope",
        "f.geometry && b.geom_4326",
        "ST_Intersects(f.geometry, b.geom_4326)",
        "ST_SimplifyPreserveTopology",
        "ST_AsMVTGeom",
        "ST_AsMVT(",
        "f.enterprise_id = :enterprise_id",
    ):
        assert required in sql
    assert "ST_Intersects(ST_Transform(f.geometry" not in sql
    assert params["enterprise_id"] == 5
    assert params["tolerance_m"] == 40


@patch.object(field_tiles, "cache_set_binary")
@patch.object(field_tiles, "cache_get_binary", return_value=b"cached-mvt")
def test_cached_tile_does_not_query_database(_cache_get, cache_set):
    db = RecordingDB(ScalarResult(b"must-not-be-used"))
    tile, cache_state, _etag = field_tiles.get_tile(
        db,
        field_tiles.resolve_scope(actor()),
        8,
        10,
        11,
    )
    assert tile == b"cached-mvt"
    assert cache_state == "HIT"
    assert db.calls == []
    cache_set.assert_not_called()


def test_openapi_exposes_authenticated_vector_contract():
    paths = app.openapi()["paths"]
    for path in (
        "/api/field-tiles/metadata",
        "/api/field-tiles/{z}/{x}/{y}.mvt",
    ):
        assert paths[path]["get"]["security"] == [{"OAuth2PasswordBearer": []}]


@patch.object(
    field_tiles_api,
    "get_tile",
    return_value=(b"fixture", "HIT", '"fixture-etag"'),
)
def test_matching_etag_returns_private_304(get_tile):
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/field-tiles/8/10/11.mvt",
            "headers": [(b"if-none-match", b'"fixture-etag"')],
        }
    )
    response = field_tiles_api.field_tile(
        request=request,
        z=8,
        x=10,
        y=11,
        enterprise_id=None,
        db=object(),
        current_user=actor(),
    )
    assert response.status_code == 304
    assert response.headers["etag"] == '"fixture-etag"'
    assert response.headers["cache-control"] == "private, max-age=300"
    get_tile.assert_called_once()


@patch.object(fields_api, "cache_delete_patterns")
def test_field_mutation_invalidation_includes_vector_namespaces(delete_patterns):
    fields_api._invalidate_field_caches(5)
    expected = fields_api.field_read_model_cache_patterns(5)
    delete_patterns.assert_called_once_with(expected)
    assert all("enterprise:6" not in pattern for pattern in expected)


def test_existing_spatial_index_is_declared_in_migration_history():
    migration = (
        Path(field_tiles_api.__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0001_baseline_existing_schema_baseline_existing_supabase_schema.py"
    )
    text = migration.read_text(encoding="utf-8")
    assert (
        'op.create_index("idx_fields_geometry", "fields", ["geometry"], '
        'postgresql_using="gist")'
    ) in text
