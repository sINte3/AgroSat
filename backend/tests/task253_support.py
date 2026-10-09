"""TASK_253 test support: a deterministic Catalog with a publication timeline.

Not collected by pytest (no ``test_`` prefix). Nothing here reaches a provider: the
Catalog answers from features the test publishes, each visible from a given day.
"""

from __future__ import annotations

import hashlib
from datetime import date
from types import SimpleNamespace

import httpx

from services import acquisition_gate as gate

START = date(2026, 10, 10)
CATALOG_URL = "https://sh.dataspace.copernicus.eu/catalog/v1/search"


def box(west, south, east, north):
    return {"type": "Polygon", "coordinates": [[[west, south], [east, south], [east, north], [west, north],
                                                [west, south]]]}


TILE = box(63.8, 39.5, 65.1, 40.6)            # a data footprint over the whole area
WEST_SWATH = box(63.8, 39.5, 64.45, 40.6)     # a swath edge crossing the area
ELSEWHERE = box(62.0, 39.5, 63.0, 40.6)       # does not touch the area at all


def md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def feature(day: date, *, tile: str = "T41SPB", cloud: float | None = 12.5, geometry=TILE, baseline="N0511",
            extra: dict | None = None) -> dict:
    """A Sentinel-2 L2A STAC item shaped like the CDSE Catalog documentation example."""
    stamp = day.strftime("%Y%m%d")
    properties = {"datetime": f"{day.isoformat()}T06:22:15Z", "platform": "sentinel-2b", "constellation": "sentinel-2",
                  "instruments": ["msi"], "gsd": 10, "proj:epsg": 32641}
    if cloud is not None:
        properties["eo:cloud_cover"] = cloud
    properties.update(extra or {})
    return {"type": "Feature", "stac_version": "1.0.0", "collection": "sentinel-2-l2a",
            "id": f"S2B_MSIL2A_{stamp}T061629_{baseline}_R034_{tile}_{stamp}T081500",
            "geometry": geometry, "properties": properties, "links": [], "assets": {}}


def _bounds(geometry):
    from shapely.geometry import shape

    return shape(geometry).bounds


class Catalog:
    """A Catalog that answers from a publication timeline: (visible_from, feature)."""

    def __init__(self, today: date = START):
        self.today = today
        self.published: list[tuple[date, dict]] = []
        self.calls: list[dict] = []
        self.counters = SimpleNamespace(requests={})
        self.fail: object = None
        self.page_hook = None

    def publish(self, item: dict, visible_from: date | None = None):
        self.published.append((visible_from or self.today, item))
        return item

    def visible(self, window_from: date, window_to: date) -> list[dict]:
        return [item for visible_from, item in self.published if visible_from <= self.today
                and window_from <= gate.parse_utc(item["properties"]["datetime"]).date() <= window_to]

    def search(self, body: dict):
        self.calls.append(body)
        self.counters.requests["catalog"] = self.counters.requests.get("catalog", 0) + 1
        self.counters.requests.setdefault("oauth", 1)
        if isinstance(self.fail, BaseException):
            raise self.fail
        if callable(self.fail):
            return self.fail(body)
        start, end = (gate.parse_utc(part) for part in body["datetime"].split("/"))
        west, south, east, north = body["bbox"]
        matches = []
        for visible_from, item in self.published:
            if visible_from > self.today:
                continue
            acquired = gate.parse_utc(item["properties"]["datetime"])
            iw, isouth, ie, inorth = _bounds(item["geometry"])
            if start <= acquired <= end and iw <= east and ie >= west and isouth <= north and inorth >= south:
                matches.append(item)
        offset = int(body.get("next", 0))
        page = matches[offset:offset + body["limit"]]
        context = {"limit": body["limit"], "returned": len(page)}
        if offset + body["limit"] < len(matches):
            context["next"] = offset + body["limit"]
        document = {"type": "FeatureCollection", "features": page, "context": context}
        if self.page_hook:
            document = self.page_hook(document)
        return httpx.Response(200, json=document, headers={"content-type": "application/geo+json"},
                              request=httpx.Request("POST", CATALOG_URL))


class FakeDate(date):
    """``date`` whose ``today()`` the test controls (patched into the collector module)."""

    current = START

    @classmethod
    def today(cls):
        return cls.current
