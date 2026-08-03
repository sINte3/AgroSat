"""Offline contract tests for TASK_194 secure NDVI raster endpoints."""

import asyncio
from datetime import date, datetime, timezone
import json
import math
import struct
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import zlib

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

def _png_chunk(kind, data):
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def rgba_png(size=256, *, visible_pixels=0, opaque=False, interlaced=False):
    pixels = bytearray(size * size * 4)
    if opaque:
        for offset in range(0, len(pixels), 4):
            pixels[offset:offset + 4] = b"\x20\x80\x40\xff"
    else:
        for index in range(visible_pixels):
            offset = index * 4
            pixels[offset:offset + 4] = b"\x20\x80\x40\xff"
    rows = b"".join(
        b"\x00" + bytes(pixels[row * size * 4:(row + 1) * size * 4])
        for row in range(size)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, int(interlaced)),
        )
        + _png_chunk(b"IDAT", zlib.compress(rows))
        + _png_chunk(b"IEND", b"")
    )


PNG_256 = rgba_png(256, opaque=True)
PNG_512 = rgba_png(512, visible_pixels=1)
PNG = PNG_512


class RasterServiceTests(unittest.TestCase):
    def setUp(self):
        from services import ndvi_raster
        self.service = ndvi_raster

    def test_versioned_cache_key_and_binary_cache_helpers(self):
        key = self.service.raster_cache_key(16, date(2026, 7, 14), 512)
        self.assertIn("16", key)
        self.assertIn("2026-07-14", key)
        self.assertIn("512", key)
        self.assertIn(self.service.PALETTE_VERSION, key)

    def test_png_validation_rejects_empty_signature_and_oversized_body(self):
        self.assertTrue(self.service.validate_png(PNG_256, 256, 256))
        self.assertFalse(self.service.validate_png(b""))
        self.assertFalse(self.service.validate_png(b"not-png"))
        self.assertFalse(
            self.service.validate_png(PNG_256 + b"x" * self.service.MAX_PNG_BYTES)
        )

    def test_exact_334_byte_fully_transparent_rgba_png_is_rejected(self):
        transparent = rgba_png(256)
        self.assertEqual(len(transparent), 334)
        self.assertFalse(self.service.validate_png(transparent, 256, 256))
        with self.assertRaises(self.service.RasterContentValidationError) as raised:
            self.service.validate_raster_content(
                transparent,
                expected_width=256,
                expected_height=256,
                max_dimension=256,
            )
        self.assertEqual(raised.exception.reason_code, "fully_transparent")

    def test_visible_and_fully_opaque_pngs_are_decoded_and_accepted(self):
        one_pixel = self.service.validate_raster_content(
            rgba_png(256, visible_pixels=1),
            expected_width=256,
            expected_height=256,
            max_dimension=256,
        )
        self.assertEqual(one_pixel.non_transparent_pixel_count, 1)
        self.assertGreater(one_pixel.visible_coverage_pct, 0)
        self.assertEqual((one_pixel.alpha_min, one_pixel.alpha_max), (0, 255))
        opaque = self.service.validate_raster_content(
            PNG_256,
            expected_width=256,
            expected_height=256,
            max_dimension=256,
        )
        self.assertEqual(opaque.non_transparent_pixel_count, 256 * 256)
        self.assertEqual(opaque.visible_coverage_pct, 100.0)
        self.assertEqual((opaque.alpha_min, opaque.alpha_max), (255, 255))

    def test_malformed_wrong_size_oversized_and_interlaced_pngs_fail_safely(self):
        cases = [
            (PNG_256[:-20], "invalid_png_chunk_structure"),
            (b"not-png", "invalid_png_signature_or_header"),
            (rgba_png(512, visible_pixels=1), "dimensions_above_maximum"),
            (rgba_png(256, visible_pixels=1, interlaced=True), "interlaced_png_not_supported"),
        ]
        for payload, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(self.service.RasterContentValidationError) as raised:
                    self.service.validate_raster_content(
                        payload,
                        expected_width=256,
                        expected_height=256,
                        max_dimension=256,
                    )
                self.assertEqual(raised.exception.reason_code, reason)
        with self.assertRaises(self.service.RasterContentValidationError) as raised:
            self.service.validate_raster_content(
                rgba_png(512, visible_pixels=1),
                expected_width=256,
                expected_height=256,
                max_dimension=1024,
            )
        self.assertEqual(raised.exception.reason_code, "dimension_mismatch")

    def test_dimensions_above_bound_are_rejected_before_decoder_allocation(self):
        payload = rgba_png(512, visible_pixels=1)
        with patch.object(self.service, "MemoryFile") as decoder:
            with self.assertRaises(self.service.RasterContentValidationError) as raised:
                self.service.validate_raster_content(
                    payload,
                    expected_width=256,
                    expected_height=256,
                    max_dimension=256,
                )
        self.assertEqual(raised.exception.reason_code, "dimensions_above_maximum")
        decoder.assert_not_called()

    def test_sanitized_metadata_contains_no_raw_body_or_pixel_array(self):
        result = self.service.validate_raster_content(
            rgba_png(256, visible_pixels=1),
            expected_width=256,
            expected_height=256,
            max_dimension=256,
        ).as_sanitized_dict()
        serialized = json.dumps(result, sort_keys=True)
        self.assertIn("nonTransparentPixelCount", result)
        self.assertIn("visibleCoveragePct", result)
        self.assertFalse(result["rawRasterPersisted"])
        self.assertFalse(result["pixelArrayPersisted"])
        self.assertNotIn("rawBytes", serialized)
        self.assertFalse(any(isinstance(value, (bytes, bytearray, list)) for value in result.values()))

    def test_process_request_uses_exact_day_l2a_geometry_mask_and_rgba_ramp(self):
        geometry = {"type": "Polygon", "coordinates": [[[64.1, 39.5], [64.2, 39.5], [64.2, 39.6], [64.1, 39.5]]]}
        payload = self.service.build_process_payload(geometry, date(2026, 7, 14), 512)
        data = payload["input"]["data"][0]
        self.assertEqual(set(payload.keys()), {"input", "output", "evalscript"})
        self.assertEqual(payload["evalscript"], self.service.EVALSCRIPT)
        self.assertNotIn("evalscript", payload["input"])
        self.assertNotIn("evalscript", data)
        self.assertEqual(data["type"], "sentinel-2-l2a")
        self.assertEqual(data["dataFilter"]["timeRange"]["from"], "2026-07-14T00:00:00Z")
        self.assertEqual(data["dataFilter"]["timeRange"]["to"], "2026-07-15T00:00:00Z")
        self.assertEqual(data["dataFilter"]["maxCloudCoverage"], 80)
        self.assertEqual(payload["input"]["bounds"]["geometry"], geometry)
        self.assertEqual(payload["output"]["width"], 512)
        self.assertEqual(payload["output"]["height"], 512)
        self.assertEqual(payload["output"]["responses"][0]["format"]["type"], "image/png")
        script = payload["evalscript"]
        self.assertIn("dataMask", script)
        self.assertIn("SCL", script)
        self.assertIn("[0,1,3,8,9,10,11]", script)
        self.assertIn("B08", script)
        self.assertIn("B04", script)
        self.assertIn("RGBA", script)

    def test_process_payload_accepts_exact_reconciled_utc_interval(self):
        payload = self.service.build_process_payload_for_interval(
            {"type": "Polygon", "coordinates": []},
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            datetime(2026, 7, 31, tzinfo=timezone.utc),
            256,
        )
        time_range = payload["input"]["data"][0]["dataFilter"]["timeRange"]
        self.assertEqual(time_range["from"], "2026-07-30T00:00:00Z")
        self.assertEqual(time_range["to"], "2026-07-31T00:00:00Z")

    def test_finite_bbox_and_deterministic_neutral_legend(self):
        bbox = self.service.validate_bbox((64.1, 39.5, 64.2, 39.6))
        self.assertEqual(bbox, [64.1, 39.5, 64.2, 39.6])
        with self.assertRaises(ValueError):
            self.service.validate_bbox((math.nan, 1, 2, 3))
        with self.assertRaises(ValueError):
            self.service.validate_bbox((2, 1, 1, 3))
        legend = self.service.LEGEND
        self.assertEqual(legend, tuple(legend))
        self.assertEqual([item["from"] for item in legend], [None, 0.1, 0.3, 0.5, 0.7])
        forbidden = ("healthy", "diseased", "irrigation", "nitrogen", "yield")
        self.assertFalse(any(word in json.dumps(legend).lower() for word in forbidden))

    def test_cached_raster_hit_avoids_upstream_and_corrupt_cache_is_replaced(self):
        with patch.object(self.service, "cache_get_binary", return_value=PNG_512), patch.object(
            self.service, "request_process_png"
        ) as upstream:
            image, state = self.service.get_raster_png(16, {"type": "Polygon", "coordinates": []}, date(2026, 7, 14), 512)
        self.assertEqual(image, PNG_512)
        self.assertEqual(state, "HIT")
        upstream.assert_not_called()
        with patch.object(self.service, "cache_get_binary", return_value=b"corrupt"), patch.object(
            self.service, "request_process_png", return_value=PNG_512
        ) as upstream, patch.object(self.service, "cache_set_binary") as cache_set:
            image, state = self.service.get_raster_png(16, {"type": "Polygon", "coordinates": []}, date(2026, 7, 14), 512)
        self.assertEqual(image, PNG_512)
        self.assertEqual(state, "MISS")
        upstream.assert_called_once()
        cache_set.assert_called_once()

    def test_redis_unavailable_degrades_and_upstream_errors_are_sanitized(self):
        with patch.object(self.service, "cache_get_binary", return_value=None), patch.object(
            self.service, "cache_set_binary", return_value=False
        ), patch.object(self.service, "request_process_png", return_value=PNG_256):
            image, state = self.service.get_raster_png(1, {"type": "Polygon", "coordinates": []}, date(2026, 7, 14), 256)
        self.assertEqual((image, state), (PNG_256, "BYPASS"))

    def test_invalid_content_timeout_and_oversized_upstream_are_rejected(self):
        geometry = {"type": "Polygon", "coordinates": []}
        bad_response = SimpleNamespace(status_code=200, headers={"content-type": "text/plain"}, content=PNG_256)
        service = Mock(is_mock=False); service.get_process_png.return_value = bad_response
        with patch.object(self.service, "get_satellite_service", return_value=service):
            with self.assertRaises(self.service.RasterUpstreamInvalid):
                self.service.request_process_png(geometry, date(2026, 7, 14), 256)
        import httpx
        service.get_process_png.side_effect = httpx.ReadTimeout("offline")
        with patch.object(self.service, "get_satellite_service", return_value=service):
            with self.assertRaises(self.service.RasterUpstreamTimeout):
                self.service.request_process_png(geometry, date(2026, 7, 14), 256)
        service.get_process_png.side_effect = None
        oversized = SimpleNamespace(status_code=200, headers={"content-type": "image/png"}, content=PNG_256 + b"x" * self.service.MAX_PNG_BYTES)
        service.get_process_png.return_value = oversized
        with patch.object(self.service, "get_satellite_service", return_value=service):
            with self.assertRaises(self.service.RasterUpstreamInvalid):
                self.service.request_process_png(geometry, date(2026, 7, 14), 256)

    def test_http_200_nonempty_transparent_png_is_insufficient(self):
        response = SimpleNamespace(
            status_code=200,
            headers={"content-type": "image/png"},
            content=rgba_png(256),
        )
        service = Mock(is_mock=False)
        service.get_process_png.return_value = response
        with patch.object(self.service, "get_satellite_service", return_value=service):
            with self.assertRaises(self.service.RasterUpstreamInvalid) as raised:
                self.service.request_process_png(
                    {"type": "Polygon", "coordinates": []},
                    date(2026, 7, 14),
                    256,
                )
        self.assertEqual(raised.exception.reason_code, "fully_transparent")

    def test_non_png_with_image_content_type_is_rejected(self):
        response = SimpleNamespace(
            status_code=200,
            headers={"content-type": "image/png"},
            content=b"not-a-png",
        )
        service = Mock(is_mock=False)
        service.get_process_png.return_value = response
        with patch.object(self.service, "get_satellite_service", return_value=service):
            with self.assertRaises(self.service.RasterUpstreamInvalid) as raised:
                self.service.request_process_png(
                    {"type": "Polygon", "coordinates": []},
                    date(2026, 7, 14),
                    256,
                )
        self.assertEqual(raised.exception.reason_code, "invalid_png_signature_or_header")

    def test_legacy_json_cache_set_success_and_exception_are_offline(self):
        from services import cache

        redis_client = Mock()
        with patch.object(cache, "CACHE_AVAILABLE", True), patch.object(cache, "_redis", redis_client):
            self.assertTrue(cache.cache_set("legacy-key", {"captured": date(2026, 7, 14)}, 60))
        redis_client.setex.assert_called_once_with(
            f"agrosat:{cache.settings.environment}:legacy-key",
            60,
            json.dumps({"captured": date(2026, 7, 14)}, default=str),
        )

        redis_client = Mock()
        redis_client.setex.side_effect = RuntimeError("offline Redis failure")
        with patch.object(cache, "CACHE_AVAILABLE", True), patch.object(cache, "_redis", redis_client):
            self.assertFalse(cache.cache_set("legacy-key", {"value": 1}, 60))


class RouterTests(unittest.TestCase):
    def setUp(self):
        from api import ndvi_raster
        self.router = ndvi_raster
        self.user = SimpleNamespace(role="viewer", enterprise_id=7)
        self.row = SimpleNamespace(
            field_id=16, observation_date=date(2026, 7, 14), satellite="Sentinel-2",
            geometry={"type": "Polygon", "coordinates": []}, west=64.1, south=39.5, east=64.2, north=39.6,
        )

    def assert_accepted_row_sql_contract(self, sql):
        self.assertIn("n.mean_ndvi IS NOT NULL", sql)
        self.assertIn("n.satellite = 'Sentinel-2'", sql)
        self.assertIn("n.satellite AS satellite", sql)
        self.assertNotIn("COALESCE", sql.upper())
        self.assertNotIn("n.mean_ndvi >", sql)
        self.assertNotIn("n.mean_ndvi >=", sql)
        self.assertNotIn("n.mean_ndvi <=", sql)
        self.assertNotIn("cloud_cover_pct", sql)

    def test_metadata_selects_accepted_latest_and_no_dml_or_lazy_loading(self):
        db = Mock()
        db.execute.return_value.fetchone.return_value = self.row
        result = asyncio.run(self.router.get_raster_metadata(16, date(2026, 7, 14), db, self.user))
        self.assertEqual(result.field_id, 16)
        self.assertEqual(result.observation_date, date(2026, 7, 14))
        sql = str(db.execute.call_args.args[0])
        self.assertIn("f.enterprise_id = :enterprise_id", sql)
        self.assertIn("n.captured_date <= :date_to", sql)
        self.assertIn("ORDER BY n.captured_date DESC", sql)
        self.assertIn("LIMIT 1", sql)
        self.assertIn("Box2D(f.geometry)", sql)
        self.assert_accepted_row_sql_contract(sql)
        db.commit.assert_not_called(); db.flush.assert_not_called(); db.add.assert_not_called()

    def test_tenant_scope_hides_foreign_field_and_global_role_has_no_tenant_clause(self):
        from fastapi import HTTPException
        db = Mock(); db.execute.return_value.fetchone.return_value = None
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(self.router.get_raster_metadata(99, date(2026, 7, 14), db, self.user))
        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.detail, "Field not found")
        db = Mock(); db.execute.return_value.fetchone.return_value = self.row
        asyncio.run(self.router.get_raster_metadata(16, date(2026, 7, 14), db, SimpleNamespace(role="admin", enterprise_id=None)))
        self.assertNotIn("enterprise_id", str(db.execute.call_args.args[0]).split("WHERE", 1)[1])

    def test_image_requires_exact_accepted_date_and_returns_safe_headers(self):
        db = Mock(); db.execute.return_value.fetchone.return_value = self.row
        with patch("api.ndvi_raster.get_raster_png", return_value=(PNG, "MISS")):
            response = asyncio.run(self.router.get_raster_image(16, date(2026, 7, 14), 512, db, self.user))
        self.assertEqual(response.media_type, "image/png")
        self.assertEqual(response.headers["X-AgroSat-Observation-Date"], "2026-07-14")
        self.assertEqual(response.headers["X-AgroSat-Raster-Cache"], "MISS")
        sql = str(db.execute.call_args.args[0])
        self.assertIn("n.captured_date = :observation_date", sql)
        self.assert_accepted_row_sql_contract(sql)
        self.assertNotIn("INSERT", sql.upper())
        self.assertNotIn("UPDATE", sql.upper())
        self.assertNotIn("DELETE", sql.upper())

    def test_mock_or_missing_service_is_sanitized_503_and_bad_size_rejected(self):
        from fastapi import HTTPException
        db = Mock(); db.execute.return_value.fetchone.return_value = self.row
        with patch("api.ndvi_raster.get_raster_png", side_effect=self.router.RasterServiceUnavailable("secret-value")):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(self.router.get_raster_image(16, date(2026, 7, 14), 512, db, self.user))
        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn("secret-value", raised.exception.detail)
        with self.assertRaises(Exception):
            asyncio.run(self.router.get_raster_image(16, date(2026, 7, 14), 300, db, self.user))

    def test_routes_are_registered_in_openapi(self):
        from main import app
        paths = app.openapi()["paths"]
        self.assertIn("/api/ndvi-raster/fields/{field_id}/metadata", paths)
        self.assertIn("/api/ndvi-raster/fields/{field_id}/image", paths)


if __name__ == "__main__":
    unittest.main()
