"""TASK_251 (J): a retried HTTP 429 never duplicates a stored observation.

Real isolated database, real NDVI child (``scripts/collect_ndvi.run``), real persistence
(``ON CONFLICT (field_id, captured_date) DO NOTHING``), real SentinelHubService with the
collector governor attached. Only the HTTP layer is simulated: the first Statistical
request is throttled (429 + Retry-After), the retry returns the field-level statistic.
"""

from __future__ import annotations

import json
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import httpx

from task225_support import Task225Base

STAT_URL = "https://sh.dataspace.copernicus.eu/statistics/v1"


class _Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _interval(day):
    # FIELD_A is ~28 ha: ~2,860 field pixels at 10 m inside a 3,600-pixel request grid.
    stats = {"sampleCount": 3600, "noDataCount": 700, "mean": 0.61, "min": 0.22, "max": 0.83,
             "stDev": 0.08, "percentiles": {"10.0": 0.50, "90.0": 0.71}}
    return {"interval": {"from": f"{day}T00:00:00Z", "to": f"{day + timedelta(days=1)}T00:00:00Z"},
            "outputs": {"ndvi": {"bands": {"B0": {"stats": stats}}},
                        "observed": {"bands": {"B0": {"stats": {"sampleCount": 3600, "noDataCount": 600}}}}}}


class RetriedRequestIdempotencyTests(Task225Base):

    def _collect(self, statistical_responses, pacer_dir, log):
        import database
        from scripts import collect_ndvi
        from services import provider_governor as pg
        from services.satellite import SentinelHubService

        service = SentinelHubService("fixture-id", "fixture-secret", provider="cdse")
        clock = _Clock()
        service.provider_governor = pg.ProviderGovernor(pg.ProviderPolicy(), Path(pacer_dir), run_id="pgtest",
                                                        stage="ndvi", clock=clock, sleep=clock.sleep)
        queue = list(statistical_responses)

        def fake_post(url, **kwargs):
            request = httpx.Request("POST", url)
            if url == service._provider_endpoints.token_url:
                return httpx.Response(200, request=request, json={"access_token": "fixture", "expires_in": 3600})
            return queue.pop(0)

        start, end = self.today - timedelta(days=14), self.today
        args = collect_ndvi.parse_args(["--write", "--field-id", str(self.field_a), "--output-log", str(log),
                                        "--date-from", start.isoformat(), "--date-to", end.isoformat()])
        with patch.object(database, "SessionLocal", self.Session), \
                patch("services.satellite.httpx.post", side_effect=fake_post), \
                patch.object(collect_ndvi, "invalidate_observation_cache", lambda _enterprise: True), \
                patch.object(collect_ndvi, "acquire_lock", return_value="lock"), \
                patch.object(collect_ndvi, "release_lock"):
            code = collect_ndvi.run(args, service_factory=lambda: service)
        return code, json.loads(Path(log).read_text(encoding="utf-8")), service.provider_governor

    def test_a_throttled_then_retried_request_stores_exactly_one_row(self):
        day = self.today - timedelta(days=3)
        ok = httpx.Response(200, request=httpx.Request("POST", STAT_URL), json={"data": [_interval(day)]})
        throttled = httpx.Response(429, request=httpx.Request("POST", STAT_URL), headers={"Retry-After": "2500"})
        with tempfile.TemporaryDirectory() as pacer:
            code, first, governor = self._collect([throttled, ok], pacer, Path(pacer) / "first.json")
            self.assertEqual(code, 0, first)
            self.assertEqual((first["inserted_count"], first["skipped_existing_count"]), (1, 0))
            self.assertEqual(governor.counters.http_429, {"statistical": 1})
            self.assertEqual(governor.counters.retries, 1)
            # The next scheduled cycle sees the same acquisition again: skipped, not duplicated.
            ok_again = httpx.Response(200, request=httpx.Request("POST", STAT_URL), json={"data": [_interval(day)]})
            code, second, _ = self._collect([ok_again], pacer, Path(pacer) / "second.json")
            self.assertEqual(code, 0, second)
            self.assertEqual((second["inserted_count"], second["skipped_existing_count"]), (0, 1))
        rows = self.sql("SELECT captured_date, mean_ndvi, valid_pixels_pct FROM ndvi_records WHERE field_id=:f",
                        {"f": self.field_a})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["captured_date"], day)
        self.assertAlmostEqual(rows[0]["mean_ndvi"], 0.61)
        self.assertEqual(self.count("autonomous_anomaly_candidates"), 0)
        self.assertEqual(self.count("alerts"), 0)
        self.assertEqual(self.count("field_inspections"), 0)

    def test_an_exhausted_retry_budget_writes_nothing_and_reports_rate_limited(self):
        throttled = [httpx.Response(429, request=httpx.Request("POST", STAT_URL), headers={"Retry-After": "1000"})
                     for _ in range(5)]
        with tempfile.TemporaryDirectory() as pacer:
            code, result, governor = self._collect(throttled, pacer, Path(pacer) / "x.json")
        self.assertEqual(code, 1)
        self.assertEqual(result["inserted_count"], 0)
        self.assertEqual(result["provider_resilience"]["last_failure"]["category"], "rate_limited")
        self.assertEqual(governor.counters.retries, 4)
        self.assertEqual(self.count("ndvi_records"), 0)
