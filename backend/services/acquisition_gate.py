"""Acquisition-aware preflight of the canonical Sentinel collector (TASK_253).

Why this exists. A daily cycle sends one Statistical API request per active field for
NDVI and one for the supporting indices (about 550 requests), each over the whole
14-day lookback, even on days when no Sentinel-2 L2A data over the area changed. On
such a day every response repeats acquisitions that are already persisted; only the
database's idempotency keeps it harmless. This module decides, before the Statistical
stages start, whether that work is provably redundant, and otherwise lets it run.

What a skip rests on (the completeness contract). One Catalog search covers the
bounding box of every active field (plus a margin) and the UTC days of the lookback
window. It counts only when every page was read and validated. The checkpoint holds
the inventory of such a search that was taken *before* a Statistical collection which
then completed for every active field in both stages (``run_status`` SUCCESS, every
batch verified by its evidence). A later cycle skips only when:

* its own search is complete;
* provider, collection, processing code and constants, indices and lookback are the
  ones the checkpoint was verified under;
* the active fields and every geometry are unchanged;
* every relevant acquisition in the current window was in the verified inventory with
  identical metadata, and no relevant verified acquisition still inside the window has
  disappeared;
* no relevant acquisition has left the window since the verified collection (policy
  ``strict``), or one that has is compensated as described under "window composition"
  (policy ``footprint``);
* the immediately preceding completed cycle is the one that last confirmed the
  checkpoint, it succeeded, and no runner retry queue is waiting;
* the periodic reconciliation is not due.

Anything else, including any doubt about the search, runs the normal collection. What
the provider documentation does not guarantee (that the Catalog lists exactly what the
Statistical API processes, when, and how a reprocessed product appears) can only delay
a collection, never lose it: the periodic reconciliation forces a full collection
within ``reconcile_days`` (at most half the lookback), while the acquisition is still in
the window.

Relevance. Under ``strict`` every listed item counts, whatever its cloud value. Under
``footprint`` an item above the Statistical requests' ``maxCloudCoverage`` 80 is recorded
but neither triggers nor compensates anything; an item without a usable cloud value
counts.

Window composition. A Statistical response's field pixel count is the largest observed
footprint among its intervals, floored by the polygon area
(``observation_quality.field_pixel_count``). When an acquisition leaves the window, that
maximum can shrink and an interval's valid share can cross the 50 % field-sample gate
without any new data. ``strict`` therefore collects whenever an acquisition leaves the
window. ``footprint`` skips anyway when a remaining acquisition day within the cloud
ceiling covers the convex hull of all fields, because such a day already observes every
field completely and reaches the maximum; that rests on the item geometry being the data
footprint, which the documentation does not state.

What a skip never does. It never writes or claims an observation, never advances a
freshness record beyond what is persisted, and never marks an acquisition processed
because it was listed: only a verified collection makes a checkpoint.

The module is pure apart from its file helpers: the collector supplies the database
scope, the governed Catalog request and the JSON writer.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

GATE_VERSION = "task253-acquisition-gate-v1"
CHECKPOINT_FILENAME = "acquisition_checkpoint.json"
CHECKPOINT_KIND = "agrosat_sentinel_acquisition_checkpoint"
CHECKPOINT_SCHEMA_VERSION = 1
MAX_CHECKPOINT_BYTES = 1024 * 1024

#: The one Sentinel-2 collection both Statistical requests read (services.satellite
#: SENTINEL_DATASET, services.satellite_collection payload type).
COLLECTION_ID = "sentinel-2-l2a"
#: The tile cloud ceiling of both Statistical requests (``maxCloudCoverage``).
RELEVANT_MAX_CLOUD_PCT = 80.0
#: Margin added to the bounding box of all active fields, in degrees (~1 km).
AOI_MARGIN_DEGREES = 0.01
CATALOG_PAGE_LIMIT = 100
MAX_CATALOG_PAGES = 10
MAX_CATALOG_PAGE_BYTES = 4 * 1024 * 1024
MAX_INVENTORY_ITEMS = CATALOG_PAGE_LIMIT * MAX_CATALOG_PAGES
#: Wall-clock budget of the whole discovery, pacing and 429 retries included.
DISCOVERY_BUDGET_SECONDS = 240.0

#: Departure policies (see the module docstring). ``strict`` relies only on documented
#: provider behaviour: every listed item counts, whatever its cloud value, and any item
#: leaving the window means a collection. ``footprint`` additionally relies on two
#: provider semantics the documentation does not state explicitly (Catalog
#: ``eo:cloud_cover`` is the tile metadata value ``maxCloudCoverage`` filters on, and an
#: item geometry is its data footprint); it must be qualified before it is used.
POLICY_STRICT = "strict"
POLICY_FOOTPRINT = "footprint"
POLICIES = (POLICY_STRICT, POLICY_FOOTPRINT)
DEFAULT_POLICY = POLICY_STRICT

DEFAULT_RECONCILE_DAYS = 3
MIN_RECONCILE_DAYS = 1
#: At most half the 14-day lookback: an acquisition the Catalog failed to show is still
#: inside the window at the forced collection, and once more if that collection fails.
MAX_RECONCILE_DAYS = 7

# Decisions.
OUTCOME_SKIP = "NO_NEW_ACQUISITIONS"
OUTCOME_COLLECT = "COLLECTION_REQUIRED"
OUTCOME_STOP = "PROVIDER_STOP"

# Why a collection is required.
REASON_NO_CHECKPOINT = "no_trusted_checkpoint"
REASON_CHECKPOINT_UNREADABLE = "checkpoint_unreadable"
REASON_CHECKPOINT_PENDING = "checkpoint_pending"
REASON_CHECKPOINT_INVALID = "checkpoint_invalid"
REASON_CONTRACT_CHANGED = "contract_changed"
REASON_SCOPE_CHANGED = "scope_changed"
REASON_SCOPE_UNAVAILABLE = "scope_unavailable"
REASON_LOOKBACK_INCOMPATIBLE = "lookback_incompatible"
REASON_CLOCK_REGRESSION = "clock_regression"
REASON_RECONCILIATION_DUE = "reconciliation_due"
REASON_CHAIN_BROKEN = "previous_cycle_not_verified"
REASON_RETRY_PENDING = "retry_queue_pending"
REASON_DISCOVERY_FAILED = "discovery_incomplete"
REASON_NEW_ACQUISITION = "new_acquisition"
REASON_ACQUISITION_CHANGED = "acquisition_changed"
REASON_ACQUISITION_REMOVED = "acquisition_removed"
REASON_WINDOW_COMPOSITION = "window_composition_changed"

# Checkpoint states.
STATE_PENDING = "pending"
STATE_VERIFIED = "verified"
STATE_INVALID = "invalid"
CHECKPOINT_STATES = (STATE_PENDING, STATE_VERIFIED, STATE_INVALID)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ITEM_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,256}$")
_REASON = re.compile(r"^[a-z_:]{1,64}$")

#: Modules whose code defines what a Statistical collection computes and persists. A
#: change in any of them (or in this gate) invalidates the checkpoint: the next cycle
#: collects in full once.
PROCESSING_MODULES = (
    "services/satellite.py",
    "services/satellite_collection.py",
    "services/satellite_indices.py",
    "services/observation_quality.py",
    "services/sentinel_grid.py",
    "services/sentinel_provider.py",
    "services/collector_idempotency.py",
    "services/acquisition_gate.py",
    "scripts/collect_ndvi.py",
    "scripts/collect_satellite_indices.py",
    "scripts/run_ndvi_collection_cycle.py",
    "scripts/run_multi_index_collection_cycle.py",
)


class GateError(ValueError):
    """A gate input the gate cannot trust; the caller collects normally."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


# ─── Contract and scope ──────────────────────────────────────────────────────


def processing_fingerprint(backend_root: Path, *, indices: Iterable[str], lookback_days: int) -> str:
    """Fingerprint of the processing semantics a checkpoint is verified under.

    Line endings are normalised so a release extracted with CRLF and a checkout with LF
    agree; any other byte of these modules counts. A missing module is refused.
    """
    digest = hashlib.sha256()
    for relative in PROCESSING_MODULES:
        path = Path(backend_root) / relative
        try:
            content = path.read_bytes().replace(b"\r\n", b"\n")
        except OSError as exc:
            raise GateError(f"processing module unreadable: {relative}") from exc
        digest.update(relative.encode("ascii") + b"\0" + _sha256(content).encode("ascii") + b"\n")
    digest.update(_canonical({
        "gate_version": GATE_VERSION,
        "collection": COLLECTION_ID,
        "relevant_max_cloud_pct": RELEVANT_MAX_CLOUD_PCT,
        "indices": sorted(set(indices)),
        "lookback_days": int(lookback_days),
    }))
    return digest.hexdigest()


def gate_contract(*, provider: str, catalog_endpoint_class: str, processing: str,
                  indices: Iterable[str], lookback_days: int, policy: str = DEFAULT_POLICY) -> dict[str, Any]:
    """The identity a checkpoint must match exactly to be used."""
    if policy not in POLICIES:
        raise GateError("unknown departure policy")
    return {
        "departure_policy": policy,
        "gate_version": GATE_VERSION,
        "provider": str(provider),
        "catalog_endpoint_class": str(catalog_endpoint_class),
        "collection": COLLECTION_ID,
        "processing_fingerprint": processing,
        "indices": sorted(set(indices)),
        "lookback_days": int(lookback_days),
        "relevant_max_cloud_pct": RELEVANT_MAX_CLOUD_PCT,
    }


@dataclass(frozen=True)
class Scope:
    """The active-field scope of one cycle, read once from the database."""

    field_ids: tuple[int, ...]
    geometry_fingerprint: str
    bbox: tuple[float, float, float, float]
    #: GeoJSON of the convex hull of every active field geometry (EPSG:4326).
    hull: dict[str, Any]

    def summary(self) -> dict[str, Any]:
        return {
            "field_count": len(self.field_ids),
            "field_ids_digest": _sha256(_canonical(list(self.field_ids))),
            "geometry_fingerprint": self.geometry_fingerprint,
        }


def scope_from_rows(rows: Iterable[tuple[int, str | None]], bbox: Any, hull_geojson: Any) -> Scope:
    """Build the scope from ``(field_id, md5 of the WKB geometry)`` rows and the extent.

    Every active field must have a geometry; one without cannot be collected anyway, so
    no fingerprint is trusted for such a scope.
    """
    pairs = []
    for field_id, geometry_md5 in rows:
        if type(field_id) is not int or field_id <= 0:
            raise GateError("active field id is malformed")
        if not isinstance(geometry_md5, str) or not re.fullmatch(r"[0-9a-f]{32}", geometry_md5):
            raise GateError("an active field has no usable geometry")
        pairs.append((field_id, geometry_md5))
    if not pairs or len({item[0] for item in pairs}) != len(pairs):
        raise GateError("active-field scope is empty or duplicated")
    pairs.sort()
    try:
        box = tuple(float(value) for value in bbox)
    except (TypeError, ValueError) as exc:
        raise GateError("active-field extent is malformed") from exc
    if len(box) != 4 or not all(math.isfinite(value) for value in box) or not (
        -180 <= box[0] <= box[2] <= 180 and -90 <= box[1] <= box[3] <= 90
    ):
        raise GateError("active-field extent is malformed")
    hull = json.loads(hull_geojson) if isinstance(hull_geojson, str) else hull_geojson
    if not isinstance(hull, dict) or hull.get("type") not in ("Polygon", "Point", "LineString", "MultiPoint"):
        raise GateError("active-field hull is malformed")
    fingerprint = _sha256("".join(f"{field_id}:{md5}\n" for field_id, md5 in pairs).encode("ascii"))
    return Scope(tuple(item[0] for item in pairs), fingerprint, box, hull)  # type: ignore[arg-type]


def discovery_bbox(scope: Scope) -> list[float]:
    west, south, east, north = scope.bbox
    margin = AOI_MARGIN_DEGREES
    return [round(max(-180.0, west - margin), 7), round(max(-90.0, south - margin), 7),
            round(min(180.0, east + margin), 7), round(min(90.0, north + margin), 7)]


# ─── Catalog discovery ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class CatalogItem:
    item_id: str
    acquired_at: datetime
    cloud_cover: float | None
    fingerprint: str
    geometry: dict[str, Any] | None

    @property
    def day(self) -> date:
        return self.acquired_at.date()

    @property
    def within_cloud_ceiling(self) -> bool:
        """Can the Statistical requests use it? Unknown cloud counts as usable."""
        return self.cloud_cover is None or self.cloud_cover <= RELEVANT_MAX_CLOUD_PCT

    def relevant(self, policy: str) -> bool:
        """Whether this item's arrival, change or departure matters under ``policy``."""
        return True if policy == POLICY_STRICT else self.within_cloud_ceiling

    def inventory_entry(self, policy: str) -> dict[str, Any]:
        return {"id": self.item_id, "day": self.day.isoformat(), "fingerprint": self.fingerprint,
                "relevant": self.relevant(policy)}


@dataclass
class Discovery:
    """The outcome of one Catalog discovery. ``complete`` is the only proof of absence."""

    complete: bool
    items: list[CatalogItem] = field(default_factory=list)
    pages: int = 0
    reason: str | None = None
    #: Provider classification of a failed request (services.satellite_safety vocabulary).
    category: str | None = None
    endpoint: str | None = None
    status_code: int | None = None

    @property
    def stop_category(self) -> str | None:
        """``authentication``/``quota`` when the failure is a provider-wide hard stop: the
        token endpoint refused the credentials, or the provider reported a quota. The
        Statistical requests would meet the same answer, so the cycle stops with that
        classification instead of sending them. A Catalog-only refusal with a valid token
        is not one: the normal collection still runs."""
        if self.category == "quota_suspected":
            return "quota"
        if self.category == "authentication" and self.endpoint == "oauth":
            return "authentication"
        return None

    def summary(self, policy: str = DEFAULT_POLICY) -> dict[str, Any]:
        relevant = [item for item in self.items if item.relevant(policy)]
        return {
            "complete": self.complete,
            "pages": self.pages,
            "items": len(self.items),
            "relevant_items": len(relevant),
            "acquisition_days": sorted({item.day.isoformat() for item in relevant}),
            "reason": self.reason,
            "category": self.category,
            "endpoint": self.endpoint,
            "status_code": self.status_code,
        }


def parse_utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip() or len(value) > 64:
        raise GateError("timestamp missing")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise GateError("timestamp invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise GateError("timestamp is not UTC")
    return parsed.astimezone(timezone.utc)


def _cloud(properties: dict[str, Any]) -> float | None:
    raw = properties.get("eo:cloud_cover")
    if raw is None or isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    return value if math.isfinite(value) and 0.0 <= value <= 100.0 else None


def _geometry(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("type") not in ("Polygon", "MultiPolygon"):
        raise GateError("item geometry is not polygonal")
    from shapely.geometry import shape

    try:
        geometry = shape(value)
    except Exception as exc:
        raise GateError("item geometry is malformed") from exc
    if geometry.is_empty:
        raise GateError("item geometry is empty")
    return value


def parse_item(feature: Any, window_from: date, window_to: date) -> CatalogItem:
    """One validated Catalog feature; anything malformed makes the page unusable."""
    if not isinstance(feature, dict):
        raise GateError("feature is not an object")
    item_id = feature.get("id")
    if not isinstance(item_id, str) or not _ITEM_ID.fullmatch(item_id):
        raise GateError("feature id is malformed")
    collection = feature.get("collection")
    if collection is not None and collection != COLLECTION_ID:
        raise GateError("feature belongs to another collection")
    properties = feature.get("properties")
    if not isinstance(properties, dict):
        raise GateError("feature properties are missing")
    acquired = parse_utc(properties.get("datetime"))
    if not window_from <= acquired.date() <= window_to:
        raise GateError("feature datetime is outside the requested window")
    geometry = _geometry(feature.get("geometry"))
    # Everything the provider says about the item counts as its identity: a reprocessed
    # or re-described acquisition is a changed acquisition. Links and assets are left
    # out; they describe access, not the data.
    fingerprint = _sha256(_canonical({"id": item_id, "properties": properties, "geometry": geometry,
                                      "bbox": feature.get("bbox")}))
    return CatalogItem(item_id, acquired, _cloud(properties), fingerprint, geometry)


def search_body(scope: Scope, date_from: date, date_to: date, *, limit: int = CATALOG_PAGE_LIMIT) -> dict[str, Any]:
    """The Catalog search over every active field and every UTC day of the window.

    The interval is closed at both ends and reaches midnight after ``date_to``, so it
    holds every acquisition the Statistical requests' ``[date_from, date_to + 1 day)``
    time range can hold (a superset never hides anything).
    """
    return {
        "collections": [COLLECTION_ID],
        "datetime": f"{date_from.isoformat()}T00:00:00Z/{(date_to + timedelta(days=1)).isoformat()}T00:00:00Z",
        "bbox": discovery_bbox(scope),
        "limit": int(limit),
    }


def _next_token(document: dict[str, Any]) -> tuple[bool, Any]:
    """``(has_next, token)`` from the STAC ``next`` link body and/or the context object.

    A ``next`` link without a usable body token, or two sources that disagree, make the
    page ambiguous; the caller treats that as an incomplete discovery.
    """
    tokens = []
    links = document.get("links")
    if links is not None:
        if not isinstance(links, list):
            raise GateError("links are malformed")
        for link in links:
            if isinstance(link, dict) and link.get("rel") == "next":
                body = link.get("body")
                if not isinstance(body, dict) or "next" not in body:
                    raise GateError("next link has no continuation token")
                tokens.append(body["next"])
    context = document.get("context")
    if isinstance(context, dict) and context.get("next") is not None:
        tokens.append(context["next"])
    if not tokens:
        return False, None
    token = tokens[0]
    if any(other != token for other in tokens[1:]):
        raise GateError("continuation tokens disagree")
    if isinstance(token, bool) or not isinstance(token, (int, str)) or (isinstance(token, str) and not 0 < len(token) <= 512):
        raise GateError("continuation token is malformed")
    return True, token


def _response_document(response: Any, page_limit: int) -> dict[str, Any]:
    content_type = str(response.headers.get("content-type", "")).split(";", 1)[0].strip().lower()
    if content_type not in ("application/json", "application/geo+json"):
        raise GateError("unexpected content type")
    content = response.content
    if not isinstance(content, (bytes, bytearray)) or len(content) > MAX_CATALOG_PAGE_BYTES:
        raise GateError("page is missing or oversized")
    try:
        document = json.loads(bytes(content).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise GateError("page is not JSON") from exc
    if not isinstance(document, dict):
        raise GateError("page is not an object")
    features = document.get("features")
    if not isinstance(features, list) or len(features) > page_limit:
        raise GateError("features are missing or exceed the page limit")
    context = document.get("context")
    if context is not None:
        if not isinstance(context, dict):
            raise GateError("context is malformed")
        returned = context.get("returned")
        if returned is not None and (type(returned) is not int or returned != len(features)):
            raise GateError("context.returned disagrees with the page")
    return document


def discover(search: Callable[[dict[str, Any]], Any], scope: Scope, date_from: date, date_to: date, *,
             page_limit: int = CATALOG_PAGE_LIMIT, max_pages: int = MAX_CATALOG_PAGES) -> Discovery:
    """Read every page of the Catalog search, or report why the result is incomplete.

    ``search(body)`` sends one governed request and returns the HTTP response. Pages are
    bounded in number and size; a page that cannot be validated, a continuation token
    that repeats or a page budget that runs out all mean "incomplete", never "empty".
    """
    from services.satellite_safety import classify_provider_error, provider_endpoint_label

    body = search_body(scope, date_from, date_to, limit=page_limit)
    window_to = date_to + timedelta(days=1)
    items: dict[str, CatalogItem] = {}
    seen_tokens: list[Any] = []
    token: Any = None
    for page in range(1, max_pages + 1):
        request = dict(body)
        if token is not None:
            request["next"] = token
        try:
            response = search(request)
        except Exception as error:  # classified, never re-raised: the caller decides
            classification = classify_provider_error(error)
            return Discovery(False, list(items.values()), page - 1, "request_failed", classification.category,
                             provider_endpoint_label(error), classification.status_code)
        status = getattr(response, "status_code", None)
        if status != 200:
            category = ("authentication" if status in (401, 403) else "quota_or_rate_limit" if status == 429
                        else "provider_unavailable" if isinstance(status, int) and 500 <= status <= 599
                        else "request_rejected")
            return Discovery(False, list(items.values()), page, "http_status", category, "catalog",
                             status if isinstance(status, int) else None)
        try:
            document = _response_document(response, page_limit)
            for feature in document["features"]:
                item = parse_item(feature, date_from, window_to)
                if item.item_id in items:
                    raise GateError("an item repeats across pages")
                items[item.item_id] = item
            if len(items) > MAX_INVENTORY_ITEMS:
                raise GateError("inventory exceeds its bound")
            has_next, token = _next_token(document)
        except GateError as exc:
            return Discovery(False, list(items.values()), page, f"malformed:{_reason_token(exc)}",
                             "invalid_response", "catalog", 200)
        if not has_next:
            return Discovery(True, sorted(items.values(), key=lambda item: (item.acquired_at, item.item_id)), page)
        if not document["features"] or token in seen_tokens:
            return Discovery(False, list(items.values()), page, "pagination_not_advancing", "invalid_response",
                             "catalog", 200)
        seen_tokens.append(token)
    return Discovery(False, list(items.values()), max_pages, "page_budget_exhausted", "invalid_response", "catalog", 200)


def _reason_token(error: BaseException) -> str:
    return re.sub(r"[^a-z_]+", "_", str(error).lower()).strip("_")[:48] or "invalid"


def covering_days(items: Iterable[CatalogItem], hull: dict[str, Any], window_from: date) -> list[str]:
    """Acquisition days inside the window whose items within the cloud ceiling together
    cover the hull of all fields (``footprint`` policy only)."""
    from shapely.geometry import shape
    from shapely.ops import unary_union

    target = shape(hull)
    by_day: dict[date, list[Any]] = {}
    for item in items:
        if item.within_cloud_ceiling and item.day >= window_from and item.geometry is not None:
            geometry = shape(item.geometry)
            by_day.setdefault(item.day, []).append(geometry if geometry.is_valid else geometry.buffer(0))
    return sorted(day.isoformat() for day, shapes in by_day.items() if unary_union(shapes).covers(target))


# ─── Checkpoint state ────────────────────────────────────────────────────────

_CONTRACT_KEYS = {"departure_policy", "gate_version", "provider", "catalog_endpoint_class", "collection", "processing_fingerprint",
                  "indices", "lookback_days", "relevant_max_cloud_pct"}
_SCOPE_KEYS = {"field_count", "field_ids_digest", "geometry_fingerprint"}
_VERIFIED_KEYS = {"full_run_id", "full_date_from", "full_date_to", "inventory", "inventory_digest",
                  "skips_since_full"}
_TOP_KEYS = {"schema_version", "kind", "status", "reason", "chain_run_id", "written_at", "contract", "scope",
             "verified"}


def inventory_digest(entries: list[dict[str, Any]]) -> str:
    return _sha256(_canonical(entries))


def _iso_date(value: Any) -> date:
    if not isinstance(value, str):
        raise GateError("date is malformed")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise GateError("date is malformed") from exc


def validate_checkpoint(document: Any) -> dict[str, Any]:
    """Strict validation; anything unexpected makes the checkpoint untrusted."""
    if not isinstance(document, dict) or set(document) != _TOP_KEYS:
        raise GateError("checkpoint keys are unexpected")
    if document["schema_version"] != CHECKPOINT_SCHEMA_VERSION or type(document["schema_version"]) is not int:
        raise GateError("checkpoint schema version is unsupported")
    if document["kind"] != CHECKPOINT_KIND or document["status"] not in CHECKPOINT_STATES:
        raise GateError("checkpoint kind or status is unexpected")
    if document["reason"] is not None and not (isinstance(document["reason"], str) and _REASON.fullmatch(document["reason"])):
        raise GateError("checkpoint reason is malformed")
    if not isinstance(document["chain_run_id"], str) or not _RUN_ID.fullmatch(document["chain_run_id"]):
        raise GateError("checkpoint run id is malformed")
    parse_utc(document["written_at"])
    contract, scope = document["contract"], document["scope"]
    if not isinstance(contract, dict) or set(contract) != _CONTRACT_KEYS:
        raise GateError("checkpoint contract is malformed")
    if not isinstance(scope, dict) or set(scope) != _SCOPE_KEYS:
        raise GateError("checkpoint scope is malformed")
    if type(scope["field_count"]) is not int or scope["field_count"] <= 0 or not all(
            isinstance(scope[key], str) and _HEX64.fullmatch(scope[key]) for key in ("field_ids_digest", "geometry_fingerprint")):
        raise GateError("checkpoint scope is malformed")
    verified = document["verified"]
    if document["status"] != STATE_VERIFIED:
        if verified is not None:
            raise GateError("only a verified checkpoint carries an inventory")
        return document
    if not isinstance(verified, dict) or set(verified) != _VERIFIED_KEYS:
        raise GateError("checkpoint verification is malformed")
    if not isinstance(verified["full_run_id"], str) or not _RUN_ID.fullmatch(verified["full_run_id"]):
        raise GateError("checkpoint full run id is malformed")
    if _iso_date(verified["full_date_from"]) > _iso_date(verified["full_date_to"]):
        raise GateError("checkpoint window is inverted")
    if type(verified["skips_since_full"]) is not int or not 0 <= verified["skips_since_full"] <= 366:
        raise GateError("checkpoint skip count is malformed")
    entries = verified["inventory"]
    if not isinstance(entries, list) or len(entries) > MAX_INVENTORY_ITEMS:
        raise GateError("checkpoint inventory is malformed")
    ids = set()
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {"id", "day", "fingerprint", "relevant"}
                or not isinstance(entry["id"], str) or not _ITEM_ID.fullmatch(entry["id"])
                or not isinstance(entry["fingerprint"], str) or not _HEX64.fullmatch(entry["fingerprint"])
                or type(entry["relevant"]) is not bool or entry["id"] in ids):
            raise GateError("checkpoint inventory entry is malformed")
        _iso_date(entry["day"])
        ids.add(entry["id"])
    if verified["inventory_digest"] != inventory_digest(entries):
        raise GateError("checkpoint inventory digest does not match")
    return document


def load_checkpoint(state_dir: Path) -> tuple[dict[str, Any] | None, str | None]:
    """``(checkpoint, None)``, ``(None, None)`` when absent, or ``(None, problem)``."""
    path = Path(state_dir) / CHECKPOINT_FILENAME
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_CHECKPOINT_BYTES + 1)
    except FileNotFoundError:
        return None, None
    except OSError:
        return None, "unreadable"
    if len(raw) > MAX_CHECKPOINT_BYTES:
        return None, "oversized"
    try:
        return validate_checkpoint(json.loads(raw.decode("utf-8"))), None
    except (UnicodeDecodeError, ValueError, GateError) as exc:
        return None, f"invalid:{_reason_token(exc)}"


def checkpoint_document(*, status: str, reason: str | None, chain_run_id: str, contract: dict[str, Any],
                        scope: Scope, verified: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
    document = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION, "kind": CHECKPOINT_KIND, "status": status, "reason": reason,
        "chain_run_id": chain_run_id, "written_at": now.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
        "contract": contract, "scope": scope.summary(), "verified": verified,
    }
    return validate_checkpoint(document)


def verified_block(*, full_run_id: str, date_from: date, date_to: date, items: Iterable[CatalogItem],
                   policy: str, skips_since_full: int = 0) -> dict[str, Any]:
    entries = [item.inventory_entry(policy) for item in sorted(items, key=lambda item: (item.acquired_at, item.item_id))]
    return {"full_run_id": full_run_id, "full_date_from": date_from.isoformat(), "full_date_to": date_to.isoformat(),
            "inventory": entries, "inventory_digest": inventory_digest(entries), "skips_since_full": skips_since_full}


# ─── Decision ────────────────────────────────────────────────────────────────


@dataclass
class Decision:
    outcome: str
    reasons: list[str]
    stop_category: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def decide(*, checkpoint: dict[str, Any] | None, checkpoint_problem: str | None, contract: dict[str, Any],
           scope: Scope | None, scope_problem: str | None, discovery: Discovery, date_from: date, date_to: date,
           reconcile_days: int, previous_run: dict[str, Any] | None, retry_pending: list[str]) -> Decision:
    """Skip only when every safety condition of the module docstring holds.

    Every failing condition is reported, so a collection always states all its reasons.
    """
    stop = discovery.stop_category
    if stop is not None:
        return Decision(OUTCOME_STOP, [REASON_DISCOVERY_FAILED], stop, {"discovery": discovery.summary()})
    reasons: list[str] = []
    detail: dict[str, Any] = {}
    if not discovery.complete:
        reasons.append(REASON_DISCOVERY_FAILED)
    if scope is None:
        reasons.append(REASON_SCOPE_UNAVAILABLE)
        detail["scope_problem"] = scope_problem
    if checkpoint is None:
        reasons.append(REASON_CHECKPOINT_UNREADABLE if checkpoint_problem else REASON_NO_CHECKPOINT)
        if checkpoint_problem:
            detail["checkpoint_problem"] = checkpoint_problem
        return Decision(OUTCOME_COLLECT, reasons, None, detail)
    if checkpoint["status"] == STATE_PENDING:
        reasons.append(REASON_CHECKPOINT_PENDING)
    elif checkpoint["status"] == STATE_INVALID:
        reasons.append(REASON_CHECKPOINT_INVALID)
        detail["checkpoint_reason"] = checkpoint["reason"]
    if checkpoint["contract"] != contract:
        reasons.append(REASON_CONTRACT_CHANGED)
        detail["contract_changes"] = sorted(key for key in _CONTRACT_KEYS
                                            if checkpoint["contract"].get(key) != contract.get(key))
    if scope is not None and checkpoint["scope"] != scope.summary():
        reasons.append(REASON_SCOPE_CHANGED)
    if previous_run is None or previous_run.get("run_id") != checkpoint["chain_run_id"] \
            or previous_run.get("status") != "succeeded":
        reasons.append(REASON_CHAIN_BROKEN)
    if retry_pending:
        reasons.append(REASON_RETRY_PENDING)
        detail["retry_pending"] = retry_pending
    verified = checkpoint["verified"]
    if checkpoint["status"] != STATE_VERIFIED or verified is None:
        return Decision(OUTCOME_COLLECT, reasons, None, detail)
    full_from, full_to = _iso_date(verified["full_date_from"]), _iso_date(verified["full_date_to"])
    days_since_full = (date_to - full_to).days
    detail["days_since_full"] = days_since_full
    detail["skips_since_full"] = verified["skips_since_full"]
    if days_since_full < 0 or date_from < full_from:
        reasons.append(REASON_CLOCK_REGRESSION if days_since_full < 0 else REASON_LOOKBACK_INCOMPATIBLE)
    elif days_since_full >= reconcile_days:
        reasons.append(REASON_RECONCILIATION_DUE)
    if discovery.complete:
        policy = contract.get("departure_policy", DEFAULT_POLICY)
        reasons.extend(_inventory_reasons(verified["inventory"], discovery.items, scope, date_from, detail, policy))
    return Decision(OUTCOME_SKIP if not reasons else OUTCOME_COLLECT, list(dict.fromkeys(reasons)), None, detail)


def _inventory_reasons(entries: list[dict[str, Any]], items: list[CatalogItem], scope: Scope | None,
                       date_from: date, detail: dict[str, Any], policy: str) -> list[str]:
    reasons: list[str] = []
    verified = {entry["id"]: entry for entry in entries}
    current = {item.item_id: item for item in items}
    new, changed, removed = [], [], []
    for item in items:
        before = verified.get(item.item_id)
        if before is None:
            if item.relevant(policy):
                new.append(item.item_id)
        elif before["fingerprint"] != item.fingerprint and (item.relevant(policy) or before["relevant"]):
            changed.append(item.item_id)
    departed = []
    for entry in entries:
        if not entry["relevant"]:
            continue
        if date.fromisoformat(entry["day"]) < date_from:
            departed.append(entry["id"])
        elif entry["id"] not in current:
            removed.append(entry["id"])
    detail.update({"new_items": new[:20], "changed_items": changed[:20], "removed_items": removed[:20],
                   "departed_items": len(departed)})
    if new:
        reasons.append(REASON_NEW_ACQUISITION)
    if changed:
        reasons.append(REASON_ACQUISITION_CHANGED)
    if removed:
        reasons.append(REASON_ACQUISITION_REMOVED)
    if departed and not (new or changed or removed):
        if policy != POLICY_FOOTPRINT or scope is None:
            reasons.append(REASON_WINDOW_COMPOSITION)
        else:
            try:
                covering = covering_days(items, scope.hull, date_from)
            except Exception:
                covering = []
            detail["covering_days"] = covering
            if not covering:
                reasons.append(REASON_WINDOW_COMPOSITION)
    return reasons


# ─── Collector evidence the decision reads ───────────────────────────────────


def previous_completed_run(output_dir: Path, filenames: Iterable[str], max_bytes: int = 64 * 1024) -> dict[str, Any] | None:
    """The newest completed run among the collector's status history files, if readable."""
    newest: tuple[datetime, dict[str, Any]] | None = None
    for name in filenames:
        path = Path(output_dir) / name
        try:
            with path.open("rb") as handle:
                raw = handle.read(max_bytes + 1)
            if len(raw) > max_bytes:
                return None
            payload = json.loads(raw.decode("utf-8"))
            started = parse_utc(payload.get("started_at"))
            run_id, status = payload.get("run_id"), payload.get("status")
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError, ValueError, GateError, AttributeError):
            return None
        if not isinstance(run_id, str) or not isinstance(status, str):
            return None
        if newest is None or started > newest[0]:
            newest = (started, {"run_id": run_id, "status": status, "started_at": started.isoformat()})
    return None if newest is None else newest[1]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class GateRun:
    """One cycle's use of the gate: ``evaluate`` before any Statistical request,
    ``finalize`` after the cycle's outcome is known (still under the canonical lock).

    Every dependency with an effect is injected: the database scope, the governed
    Catalog client, the JSON writer and the clock. ``evaluate`` never raises; whatever
    it cannot establish becomes a reason to collect.
    """

    def __init__(self, *, state_dir: Path, output_dir: Path, run_id: str, indices: Iterable[str],
                 lookback_days: int, date_from: date, date_to: date, reconcile_days: int, policy: str,
                 backend_root: Path,
                 provider_identity: Callable[[], tuple[str, str]], scope_resolver: Callable[[], Scope],
                 search_factory: Callable[[], tuple[Callable[[dict[str, Any]], Any], Any]],
                 write_json: Callable[[Path, dict[str, Any]], None], history_files: Iterable[str],
                 retry_files: Iterable[str], clock: Callable[[], datetime] = utc_now):
        self.state_dir = Path(state_dir)
        self.output_dir = Path(output_dir)
        self.run_id = run_id
        self.indices = sorted(set(indices))
        self.lookback_days = int(lookback_days)
        self.date_from, self.date_to = date_from, date_to
        self.reconcile_days = int(reconcile_days)
        self.policy = policy
        self.backend_root = Path(backend_root)
        self.provider_identity = provider_identity
        self.scope_resolver = scope_resolver
        self.search_factory = search_factory
        self.write_json = write_json
        self.history_files = tuple(history_files)
        self.retry_files = tuple(retry_files)
        self.clock = clock
        self.contract: dict[str, Any] | None = None
        self.scope: Scope | None = None
        self.discovery = Discovery(False, reason="not_started")
        self.decision: Decision | None = None
        self.previous_verified: dict[str, Any] | None = None
        self.checkpoint_before: str | None = None
        self.checkpoint_after: str | None = None
        self.requests: dict[str, int] = {}
        self.problems: list[str] = []
        self.seconds = 0.0

    @property
    def path(self) -> Path:
        return self.state_dir / CHECKPOINT_FILENAME

    def evaluate(self, active_ids: Iterable[int]) -> Decision:
        started = self.clock()
        try:
            decision = self._evaluate(list(active_ids))
        except Exception as exc:  # a gate defect must never stop or skip a collection
            self.problems.append(f"gate_error:{type(exc).__name__}")
            decision = Decision(OUTCOME_COLLECT, ["gate_error"])
        self.decision = decision
        self.seconds = round((self.clock() - started).total_seconds(), 3)
        return decision

    def _evaluate(self, active_ids: list[int]) -> Decision:
        try:
            provider, endpoint_class = self.provider_identity()
            self.contract = gate_contract(
                provider=provider, catalog_endpoint_class=endpoint_class,
                processing=processing_fingerprint(self.backend_root, indices=self.indices,
                                                  lookback_days=self.lookback_days),
                indices=self.indices, lookback_days=self.lookback_days, policy=self.policy)
        except Exception as exc:
            self.problems.append(f"contract_unavailable:{type(exc).__name__}")
        scope_problem = None
        try:
            scope = self.scope_resolver()
            if list(scope.field_ids) != sorted(active_ids):
                raise GateError("scope differs from the cycle's active fields")
            self.scope = scope
        except Exception as exc:
            scope_problem = _reason_token(exc) if isinstance(exc, GateError) else type(exc).__name__
        checkpoint, checkpoint_problem = load_checkpoint(self.state_dir)
        self.checkpoint_before = checkpoint["status"] if checkpoint else checkpoint_problem or "absent"
        if checkpoint is not None and checkpoint["status"] == STATE_VERIFIED:
            self.previous_verified = checkpoint["verified"]
        previous = previous_completed_run(self.output_dir, self.history_files)
        retry = pending_retry_queues(self.state_dir, self.retry_files)
        if self.scope is not None:
            self.discovery = self._discover()
        else:
            self.discovery = Discovery(False, reason="scope_unavailable")
        decision = decide(
            checkpoint=checkpoint, checkpoint_problem=checkpoint_problem,
            contract=self.contract or {"unavailable": True}, scope=self.scope, scope_problem=scope_problem,
            discovery=self.discovery, date_from=self.date_from, date_to=self.date_to,
            reconcile_days=self.reconcile_days, previous_run=previous, retry_pending=retry)
        if self.contract is None and decision.outcome == OUTCOME_SKIP:
            decision = Decision(OUTCOME_COLLECT, [REASON_CONTRACT_CHANGED], None, decision.detail)
        decision.detail["previous_run"] = previous
        # Invalidate before any Statistical work: only finalize() can make it trusted again,
        # so an interrupted cycle always leaves an untrusted checkpoint behind.
        if self.contract is not None and self.scope is not None:
            try:
                self.write_json(self.path, checkpoint_document(
                    status=STATE_PENDING, reason=None, chain_run_id=self.run_id, contract=self.contract,
                    scope=self.scope, verified=None, now=self.clock()))
            except Exception as exc:
                self.problems.append(f"pending_write_failed:{type(exc).__name__}")
                if decision.outcome == OUTCOME_SKIP:
                    decision = Decision(OUTCOME_COLLECT, ["checkpoint_unwritable"], None, decision.detail)
        elif decision.outcome == OUTCOME_SKIP:  # unreachable by decide(); kept fail-closed
            decision = Decision(OUTCOME_COLLECT, [REASON_SCOPE_UNAVAILABLE], None, decision.detail)
        return decision

    def _discover(self) -> Discovery:
        from services.satellite_safety import classify_provider_error, provider_endpoint_label

        governor = None
        try:
            search, governor = self.search_factory()
        except Exception as error:
            classification = classify_provider_error(error)
            return Discovery(False, reason="client_unavailable", category=classification.category,
                             endpoint=provider_endpoint_label(error), status_code=classification.status_code)
        try:
            return discover(search, self.scope, self.date_from, self.date_to)
        finally:
            counters = getattr(governor, "counters", None)
            requests = getattr(counters, "requests", None)
            if isinstance(requests, dict):
                self.requests = {key: int(value) for key, value in requests.items() if isinstance(value, int)}

    def finalize(self, *, final_code: int, run_status: str) -> None:
        """Write the checkpoint this cycle has earned: verified, or invalid with a reason."""
        if self.decision is None or self.contract is None or self.scope is None:
            return
        outcome = self.decision.outcome
        verified = None
        reason = None
        if outcome == OUTCOME_SKIP and final_code == 0 and run_status == OUTCOME_SKIP and self.previous_verified:
            verified = {**self.previous_verified, "skips_since_full": self.previous_verified["skips_since_full"] + 1}
        elif outcome == OUTCOME_COLLECT and final_code == 0 and run_status == "SUCCESS" and self.discovery.complete:
            verified = verified_block(full_run_id=self.run_id, date_from=self.date_from, date_to=self.date_to,
                                      items=self.discovery.items, policy=self.policy)
        elif outcome == OUTCOME_STOP:
            reason = f"provider_stop:{self.decision.stop_category}"
        elif outcome == OUTCOME_COLLECT and not self.discovery.complete:
            reason = REASON_DISCOVERY_FAILED
        else:
            reason = f"cycle_not_verified:{str(run_status).lower()}"[:64]
        status = STATE_VERIFIED if verified is not None else STATE_INVALID
        self.write_json(self.path, checkpoint_document(
            status=status, reason=reason, chain_run_id=self.run_id, contract=self.contract, scope=self.scope,
            verified=verified, now=self.clock()))
        self.checkpoint_after = status if reason is None else f"{status}:{reason}"

    def report(self) -> dict[str, Any]:
        decision = self.decision
        outcome = decision.outcome if decision else None
        return {
            "enabled": True,
            "evaluated": decision is not None,
            "version": GATE_VERSION,
            "outcome": outcome,
            "reasons": decision.reasons if decision else [],
            "stop_category": decision.stop_category if decision else None,
            "statistical_collection": "skipped" if outcome == OUTCOME_SKIP
            else "not_started" if outcome == OUTCOME_STOP else "performed",
            "detail": decision.detail if decision else {},
            "discovery": self.discovery.summary(self.policy),
            "requests": dict(self.requests),
            "reconcile_days": self.reconcile_days,
            "departure_policy": self.policy,
            "window": {"date_from": self.date_from.isoformat(), "date_to": self.date_to.isoformat()},
            "checkpoint_before": self.checkpoint_before,
            "checkpoint_after": self.checkpoint_after,
            "problems": self.problems[:10],
            "seconds": self.seconds,
        }


def pending_retry_queues(state_dir: Path, filenames: Iterable[str], max_bytes: int = 64 * 1024) -> list[str]:
    """Runner state files whose retry queue still holds fields (or that cannot be read)."""
    pending = []
    for name in filenames:
        path = Path(state_dir) / name
        try:
            with path.open("rb") as handle:
                raw = handle.read(max_bytes + 1)
            payload = json.loads(raw.decode("utf-8")) if len(raw) <= max_bytes else None
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError, ValueError):
            payload = None
        queue = payload.get("retry_field_ids") if isinstance(payload, dict) else None
        if not isinstance(queue, list):
            pending.append(f"{name}:unreadable")
        elif queue:
            pending.append(f"{name}:{len(queue)}")
    return pending
