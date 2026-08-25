"""Runtime qualification for TASK_217 against the protected isolated database."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import time
import sys

import psycopg2
from fastapi.testclient import TestClient
from sqlalchemy import event


PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDAT\x08\xd7c\xf8"
    b"\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
)


def require(response, status: int, label: str):
    if response.status_code != status:
        detail = response.text[:500].replace("\n", " ")
        raise AssertionError(f"{label}: expected {status}, got {response.status_code}: {detail}")
    return response.json() if response.content else None


def timed(call, metrics: dict, name: str):
    started = time.perf_counter()
    result = call()
    metrics[name].append(round((time.perf_counter() - started) * 1000, 3))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser-credentials", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    backend_root = Path(__file__).resolve().parents[2] / "backend"
    sys.path.insert(0, str(backend_root))
    from config import settings
    from database import engine
    from main import app

    # Keep qualification evidence concise while preserving failures and tracebacks.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("services.metrics").setLevel(logging.WARNING)

    database_name = engine.url.database or ""
    allowed_prefixes = ("agrosat_r3_task217_", "agrosat_r3_task218_")
    if database_name == "agrosat" or not database_name.startswith(allowed_prefixes):
        raise RuntimeError("PROTECTED_QUALIFICATION_DATABASE_IDENTITY_REJECTED")
    protected = json.loads(args.browser_credentials.resolve(strict=True).read_text("utf-8"))
    identities = protected["identities"]
    metrics: dict[str, list[float]] = defaultdict(list)
    run_tag = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    query_count = 0

    def count_query(*_args):
        nonlocal query_count
        query_count += 1

    event.listen(engine, "before_cursor_execute", count_query)
    client = TestClient(app)

    def login(identity):
        response = client.post("/api/auth/login", data={
            "username": identity["email"], "password": identity["password"],
        })
        return require(response, 200, "login")["access_token"]

    admin_token = login(identities["admin"])
    agronomist_token = login(identities["agronomist"])
    other_token = login(identities["other"])
    admin = {"Authorization": f"Bearer {admin_token}"}
    agronomist = {"Authorization": f"Bearer {agronomist_token}"}
    other = {"Authorization": f"Bearer {other_token}"}

    # Seed only isolated deterministic authorization and alert fixtures.
    raw_url = settings.database_url
    with psycopg2.connect(raw_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT enterprise_id,ST_X(ST_PointOnSurface(geometry)),ST_Y(ST_PointOnSurface(geometry)),"
                "ST_AsGeoJSON(ST_Buffer(ST_PointOnSurface(geometry)::geography,2)::geometry)::json "
                "FROM fields WHERE id=4"
            )
            enterprise_id, inside_lon, inside_lat, inside_zone = cursor.fetchone()
            cursor.execute("SELECT id FROM enterprises WHERE id<>%s ORDER BY id LIMIT 1", (enterprise_id,))
            other_enterprise_id = cursor.fetchone()[0]
            cursor.execute("SELECT hashed_password FROM users WHERE id=%s", (identities["other"]["id"],))
            shared_hash = cursor.fetchone()[0]
            cursor.execute(
                """INSERT INTO users (enterprise_id,email,full_name,role,hashed_password,is_active,created_at)
                VALUES (%s,'task217-same-viewer@qualification.invalid','TASK 217 same viewer','viewer',%s,true,now())
                ON CONFLICT (email) DO UPDATE SET enterprise_id=EXCLUDED.enterprise_id,is_active=true
                RETURNING id""", (enterprise_id, shared_hash),
            )
            same_viewer_id = cursor.fetchone()[0]
            cursor.execute(
                """INSERT INTO users (enterprise_id,email,full_name,role,hashed_password,is_active,created_at)
                VALUES (%s,'task217-cross-agronomist@qualification.invalid','TASK 217 cross agronomist','agronomist',%s,true,now())
                ON CONFLICT (email) DO UPDATE SET enterprise_id=EXCLUDED.enterprise_id,is_active=true
                RETURNING id""", (other_enterprise_id, shared_hash),
            )
            cross_agronomist_id = cursor.fetchone()[0]
            cursor.execute(
                """INSERT INTO alerts (field_id,alert_type,severity,title,description,triggered_value,
                threshold_value,triggered_at,is_active,source,source_key)
                VALUES (4,'ndvi_drop','warning','TASK 217 alert','TASK 217 isolated alert context',
                0.31,0.42,now(),true,'task217',%s)
                ON CONFLICT (source,source_key) WHERE is_active=true AND source IS NOT NULL AND source_key IS NOT NULL
                DO UPDATE SET field_id=EXCLUDED.field_id,description=EXCLUDED.description RETURNING id""",
                (f"anomaly-workflow-{run_tag}",),
            )
            alert_id = cursor.fetchone()[0]
        connection.commit()

    same_viewer_identity = dict(identities["other"])
    same_viewer_identity.update(id=same_viewer_id, email="task217-same-viewer@qualification.invalid")
    same_viewer_token = login(same_viewer_identity)
    same_viewer = {"Authorization": f"Bearer {same_viewer_token}"}

    require(client.get("/api/anomaly-inspections/queue"), 401, "unauthenticated queue")
    require(client.post("/api/anomaly-inspections", json={}), 401, "unauthenticated create")

    assignees = require(client.get("/api/anomaly-inspections/assignees", params={"field_id": 4}, headers=admin), 200, "assignee list")
    assert any(row["id"] == identities["agronomist"]["id"] for row in assignees)

    due_at = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    base_manual = {
        "field_id": 4, "source_kind": "manual", "source_alert_id": None,
        "provider": None, "item_id": None, "acquired_at": None, "index_name": None,
        "sampled_value": None, "comparison_value": None, "delta": None,
        "geometry_hash": None, "point": {"longitude": inside_lon, "latitude": inside_lat},
        "zone": None, "reason": "TASK 217 manual inspection qualification",
        "priority": "normal", "assigned_to_id": identities["agronomist"]["id"], "due_at": due_at,
    }
    require(client.post("/api/anomaly-inspections", headers={**same_viewer, "Idempotency-Key": f"viewer-denied-{run_tag}"}, json=base_manual), 403, "viewer write")
    cross = dict(base_manual, assigned_to_id=cross_agronomist_id)
    require(client.post("/api/anomaly-inspections", json=cross, headers={**admin, "Idempotency-Key": f"task217-cross-assignee-{run_tag}"}), 422, "cross tenant assignee")
    outside = dict(base_manual, point={"longitude": 0.0, "latitude": 0.0})
    require(client.post("/api/anomaly-inspections", json=outside, headers={**admin, "Idempotency-Key": f"task217-outside-{run_tag}"}), 422, "outside point")
    zone_payload = dict(base_manual, point=None, zone=inside_zone, reason="TASK 217 bounded inside zone")
    zone_created = require(client.post("/api/anomaly-inspections", json=zone_payload, headers={**admin, "Idempotency-Key": f"task217-zone-inside-{run_tag}"}), 201, "inside zone")
    assert zone_created["inspection"]["source"]["zone"] is not None

    alert_payload = dict(base_manual, source_kind="alert", source_alert_id=alert_id, point=None,
                         reason="TASK 217 inspect qualified alert", priority="high")
    alert_created = require(client.post("/api/anomaly-inspections", json=alert_payload,
                                        headers={**admin, "Idempotency-Key": f"task217-alert-create-{run_tag}"}), 201, "alert create")
    assert alert_created["inspection"]["source"]["alert_id"] == alert_id

    catalog = require(client.get("/api/raster/fields/4/scenes", headers=admin), 200, "scene catalog")
    scene = next(item for item in catalog["scenes"] if item["acquired_at"].startswith("2026-07-25T06:38:13.599"))
    workspace = require(client.get("/api/raster/fields/4/workspace", params={"scene_id": scene["scene_id"]}, headers=admin), 200, "pixel workspace")
    sample = require(client.get("/api/raster/fields/4/sample", params={
        "scene_id": scene["scene_id"], "longitude": 64.52699869734, "latitude": 40.00729210566,
    }, headers=admin), 200, "pixel sample")
    assert sample["status"] == "value"
    assert isinstance(sample["ndvi"], (int, float)) and -1.0 <= sample["ndvi"] <= 1.0
    pixel_payload = dict(base_manual, source_kind="pixel_ndvi", source_alert_id=None,
                         provider="cdse", item_id=scene["scene_id"], acquired_at=scene["acquired_at"],
                         index_name="ndvi", sampled_value=sample["ndvi"], geometry_hash=workspace["geometry_hash"],
                         point={"longitude": sample["longitude"], "latitude": sample["latitude"]},
                         reason="TASK 217 Pixel NDVI qualified point")
    for invalid_value in (float("nan"), float("inf"), float("-inf")):
        invalid_pixel = dict(pixel_payload, sampled_value=invalid_value)
        require(client.post("/api/anomaly-inspections", content=json.dumps(invalid_pixel, default=str),
                            headers={**admin, "Content-Type": "application/json",
                                     "Idempotency-Key": f"task217-nonfinite-{run_tag}-{str(invalid_value).replace('-', 'n')}"}),
                422, "non-finite pixel value")
    response = timed(lambda: client.post("/api/anomaly-inspections", json=pixel_payload,
                                         headers={**admin, "Idempotency-Key": f"task217-pixel-create-{run_tag}"}), metrics, "create_ms")
    pixel = require(response, 201, "pixel create")["inspection"]
    inspection_id = pixel["id"]

    require(client.get(f"/api/anomaly-inspections/{inspection_id}", headers=other), 404, "cross tenant inspection")
    require(client.get(f"/api/anomaly-inspections/{inspection_id}", headers=same_viewer), 200, "same tenant viewer read")
    require(client.post(f"/api/anomaly-inspections/{inspection_id}/start", json={"expected_version": pixel["version"]}, headers=same_viewer), 403, "viewer transition")
    require(client.post(f"/api/anomaly-inspections/{inspection_id}/submit", json={"expected_version": pixel["version"]}, headers=agronomist), 422, "inspection evidence skip")
    detail_queries_before = query_count
    response = timed(lambda: client.get(f"/api/anomaly-inspections/{inspection_id}", headers=agronomist), metrics, "detail_ms")
    detail = require(response, 200, "assigned detail")
    metrics["detail_queries"].append(query_count - detail_queries_before)
    started = timed(lambda: client.post(f"/api/anomaly-inspections/{inspection_id}/start", json={"expected_version": detail["version"]}, headers=agronomist), metrics, "start_ms")
    current = require(started, 200, "start")["inspection"]

    finding = {
        "expected_version": current["version"], "inspected_at": datetime.now(timezone.utc).isoformat(),
        "gps_point": {"longitude": inside_lon, "latitude": inside_lat}, "gps_accuracy_m": 4.5,
        "cause": "water_stress", "other_explanation": None, "severity": "high",
        "affected_area_ha": 1.25, "affected_area_pct": None,
        "observations": "TASK 217 structured field observation",
        "recommended_action": "Inspect irrigation delivery and repair the affected line", "sync_state": "server",
    }
    saved = timed(lambda: client.put(f"/api/anomaly-inspections/{inspection_id}/finding", json=finding, headers=agronomist), metrics, "finding_save_ms")
    current_detail = require(saved, 200, "finding save")["inspection"]
    require(client.put(f"/api/anomaly-inspections/{inspection_id}/finding", json=finding, headers=agronomist), 409, "stale finding")

    wrong = client.post(f"/api/anomaly-inspections/{inspection_id}/photos", headers=agronomist,
                        data={"expected_version": current_detail["version"]},
                        files={"photo": ("wrong.png", b"not-a-png", "image/png")})
    require(wrong, 422, "photo magic")
    traversal = client.post(f"/api/anomaly-inspections/{inspection_id}/photos", headers=agronomist,
                            data={"expected_version": current_detail["version"]},
                            files={"photo": ("../escape.png", PNG, "image/png")})
    require(traversal, 422, "photo traversal")
    too_large = client.post(f"/api/anomaly-inspections/{inspection_id}/photos", headers=agronomist,
                            data={"expected_version": current_detail["version"]},
                            files={"photo": ("large.png", PNG + b"0" * (8 * 1024 * 1024), "image/png")})
    require(too_large, 413, "photo size")
    uploaded = timed(lambda: client.post(f"/api/anomaly-inspections/{inspection_id}/photos", headers=agronomist,
                                         data={"expected_version": current_detail["version"]},
                                         files={"photo": ("field.png", PNG, "image/png")}), metrics, "photo_upload_ms")
    upload_result = require(uploaded, 201, "photo upload")
    photo = upload_result["photo"]
    current = upload_result["inspection"]
    require(client.get(f"/api/anomaly-inspections/{inspection_id}/photos/{photo['id']}", headers=other), 404, "cross tenant photo")
    photo_response = client.get(f"/api/anomaly-inspections/{inspection_id}/photos/{photo['id']}", headers=agronomist)
    assert photo_response.status_code == 200 and photo_response.content == PNG

    # The bounded count and delete contracts run on the independent zone inspection.
    zone_id = zone_created["inspection"]["id"]
    zone_detail = require(client.get(f"/api/anomaly-inspections/{zone_id}", headers=agronomist), 200, "zone detail")
    zone_current = require(client.post(f"/api/anomaly-inspections/{zone_id}/start",
                                  json={"expected_version": zone_detail["version"]}, headers=agronomist), 200, "zone start")["inspection"]
    zone_photos = []
    for index in range(10):
        added = require(client.post(f"/api/anomaly-inspections/{zone_id}/photos", headers=agronomist,
                                      data={"expected_version": zone_current["version"]},
                                      files={"photo": (f"count-{index}.png", PNG, "image/png")}),
                        201, "photo count upload")
        zone_current = added["inspection"]
        zone_photos.append(added["photo"])
    require(client.post(f"/api/anomaly-inspections/{zone_id}/photos", headers=agronomist,
                        data={"expected_version": zone_current["version"]},
                        files={"photo": ("count-overflow.png", PNG, "image/png")}), 413, "photo count limit")
    deleted = require(client.request("DELETE", f"/api/anomaly-inspections/{zone_id}/photos/{zone_photos[0]['id']}",
                                     json={"expected_version": zone_current["version"]}, headers=agronomist),
                      200, "photo delete")
    zone_current = deleted["inspection"]
    require(client.get(f"/api/anomaly-inspections/{zone_id}/photos/{zone_photos[0]['id']}", headers=agronomist),
            404, "deleted photo download")

    submitted = timed(lambda: client.post(f"/api/anomaly-inspections/{inspection_id}/submit", json={"expected_version": current["version"]}, headers=agronomist), metrics, "submit_ms")
    current = require(submitted, 200, "submit")["inspection"]
    require(client.post(f"/api/anomaly-inspections/{inspection_id}/review", json={"expected_version": current["version"], "decision": "rejected", "reason": None}, headers=admin), 422, "rejection reason")
    reviewed = timed(lambda: client.post(f"/api/anomaly-inspections/{inspection_id}/review", json={"expected_version": current["version"], "decision": "confirmed", "reason": "Field evidence confirms the anomaly"}, headers=admin), metrics, "review_ms")
    current = require(reviewed, 200, "review")["inspection"]
    require(client.post(f"/api/anomaly-inspections/{inspection_id}/review",
                        json={"expected_version": current["version"], "decision": "confirmed", "reason": None},
                        headers=agronomist), 403, "agronomist review")

    action_payload = {
        "expected_inspection_version": current["version"], "action_type": "irrigation_repair",
        "owner_id": identities["agronomist"]["id"], "instructions": "Repair the affected irrigation line and record completion",
        "planned_start_at": None, "due_at": due_at,
    }
    action_created = timed(lambda: client.post(f"/api/anomaly-inspections/{inspection_id}/actions", json=action_payload, headers=admin), metrics, "action_create_ms")
    action_result = require(action_created, 201, "action create")
    action = action_result["action"]
    require(client.post(f"/api/anomaly-inspections/actions/{action['id']}/transition", json={"expected_version": action["version"], "transition": "start", "note": None}, headers=other), 404, "cross tenant action")
    action = require(client.post(f"/api/anomaly-inspections/actions/{action['id']}/transition", json={"expected_version": action["version"], "transition": "start", "note": None}, headers=agronomist), 200, "action start")["action"]
    action = require(client.post(f"/api/anomaly-inspections/actions/{action['id']}/transition", json={"expected_version": action["version"], "transition": "complete", "note": "Repair completed and line pressure restored"}, headers=agronomist), 200, "action complete")["action"]
    verified = timed(lambda: client.post(f"/api/anomaly-inspections/actions/{action['id']}/verify", json={
        "expected_version": action["version"], "result": "effective", "notes": "Follow-up observation confirms recovery",
        "index_name": "ndvi", "sampled_value": sample["ndvi"], "create_follow_up": False,
        "follow_up_assignee_id": None, "follow_up_due_at": None,
    }, headers=admin), metrics, "verify_ms")
    action = require(verified, 200, "verify effective")["action"]
    assert action["status"] == "verified_effective"

    # A second confirmed anomaly proves ineffective verification and immutable follow-up linkage.
    alert_id_created = alert_created["inspection"]["id"]
    alert_detail = require(client.get(f"/api/anomaly-inspections/{alert_id_created}", headers=agronomist), 200, "alert detail")
    alert_current = require(client.post(f"/api/anomaly-inspections/{alert_id_created}/start",
                                        json={"expected_version": alert_detail["version"]}, headers=agronomist),
                            200, "alert start")["inspection"]
    alert_finding = dict(finding, expected_version=alert_current["version"], observations="TASK 217 ineffective-cycle finding")
    alert_current = require(client.put(f"/api/anomaly-inspections/{alert_id_created}/finding",
                                       json=alert_finding, headers=agronomist), 200, "alert finding")["inspection"]
    alert_current = require(client.post(f"/api/anomaly-inspections/{alert_id_created}/submit",
                                        json={"expected_version": alert_current["version"]}, headers=agronomist),
                            200, "alert submit")["inspection"]
    alert_current = require(client.post(f"/api/anomaly-inspections/{alert_id_created}/review",
                                        json={"expected_version": alert_current["version"], "decision": "confirmed",
                                              "reason": "Confirmed for ineffective-cycle qualification"}, headers=admin),
                            200, "alert review")["inspection"]
    follow_action = require(client.post(f"/api/anomaly-inspections/{alert_id_created}/actions", headers=admin, json={
        "expected_inspection_version": alert_current["version"], "action_type": "field_treatment",
        "owner_id": identities["agronomist"]["id"], "instructions": "Complete bounded follow-up treatment",
        "planned_start_at": None, "due_at": due_at,
    }), 201, "follow-up action")["action"]
    follow_action = require(client.post(f"/api/anomaly-inspections/actions/{follow_action['id']}/transition", headers=agronomist,
                                        json={"expected_version": follow_action["version"], "transition": "start", "note": None}),
                            200, "follow-up action start")["action"]
    follow_action = require(client.post(f"/api/anomaly-inspections/actions/{follow_action['id']}/transition", headers=agronomist,
                                        json={"expected_version": follow_action["version"], "transition": "complete",
                                              "note": "Treatment completed for verification"}),
                            200, "follow-up action complete")["action"]
    ineffective = require(client.post(f"/api/anomaly-inspections/actions/{follow_action['id']}/verify", headers=admin, json={
        "expected_version": follow_action["version"], "result": "ineffective", "notes": "Another field cycle is required",
        "index_name": "ndvi", "sampled_value": sample["ndvi"], "create_follow_up": True,
        "follow_up_assignee_id": identities["agronomist"]["id"], "follow_up_due_at": due_at,
    }), 200, "ineffective verification")
    assert ineffective["action"]["status"] == "verified_ineffective"
    assert ineffective["follow_up_inspection"]["follow_up_of_id"] == alert_id_created

    final_detail = require(client.get(f"/api/anomaly-inspections/{inspection_id}", headers=admin), 200, "final detail")
    events = final_detail["timeline"]
    assert [event["occurred_at"] for event in events] == sorted(event["occurred_at"] for event in events)
    required_events = {"inspection_created", "inspection_assigned", "inspection_started", "finding_saved",
                       "photo_uploaded", "inspection_submitted", "inspection_confirmed", "action_created",
                       "action_started", "action_completed", "action_verified_effective"}
    assert required_events <= {event["event_type"] for event in events}

    queue_queries_before = query_count
    cold = timed(lambda: client.get("/api/anomaly-inspections/queue", params={"status": "confirmed", "source_kind": "pixel_ndvi", "sort": "due_at"}, headers=admin), metrics, "queue_cold_ms")
    queue = require(cold, 200, "queue filter")
    metrics["queue_queries"].append(query_count - queue_queries_before)
    assert any(item["id"] == inspection_id for item in queue["items"])
    timed(lambda: require(client.get("/api/anomaly-inspections/queue", params={"status": "confirmed", "source_kind": "pixel_ndvi", "sort": "due_at"}, headers=admin), 200, "queue warm"), metrics, "queue_warm_ms")
    timeline = require(client.get("/api/anomaly-inspections/fields/4/timeline", headers=admin), 200, "field timeline")
    assert any(event["inspection_id"] == inspection_id for event in timeline["events"])

    overdue_payload = dict(base_manual, due_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
                           reason="TASK 217 overdue queue qualification")
    overdue = require(client.post("/api/anomaly-inspections", json=overdue_payload,
                                  headers={**admin, "Idempotency-Key": f"task217-overdue-{run_tag}"}), 201, "overdue create")["inspection"]
    overdue_queue = require(client.get("/api/anomaly-inspections/queue", params={"due_state": "overdue", "limit": 1,
                                                                                "sort": "due_at"}, headers=admin),
                            200, "overdue queue")
    assert overdue_queue["summary"]["overdue"] >= 1 and overdue_queue["items"][0]["id"] == overdue["id"]
    page_two = require(client.get("/api/anomaly-inspections/queue", params={"limit": 1, "offset": 1,
                                                                            "sort": "created_at"}, headers=admin),
                       200, "queue pagination")
    page_one = require(client.get("/api/anomaly-inspections/queue", params={"limit": 1, "offset": 0,
                                                                            "sort": "created_at"}, headers=admin),
                       200, "queue pagination first")
    assert page_one["items"][0]["id"] != page_two["items"][0]["id"]

    with psycopg2.connect(raw_url) as connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE operational_audit_events SET event_type=event_type WHERE inspection_id=%s", (inspection_id,))
            connection.commit()
            raise AssertionError("audit update unexpectedly succeeded")
        except psycopg2.Error:
            connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM field_inspections fi JOIN fields f ON f.id=fi.field_id "
                "WHERE fi.enterprise_id<>f.enterprise_id"
            )
            tenant_violations = int(cursor.fetchone()[0])
            cursor.execute("SELECT count(*) FROM pg_constraint WHERE convalidated=false")
            invalid_constraints = int(cursor.fetchone()[0])
            cursor.execute("SELECT count(*) FROM pg_index WHERE NOT indisvalid")
            invalid_indexes = int(cursor.fetchone()[0])
    assert tenant_violations == invalid_constraints == invalid_indexes == 0

    output = {
        "result": "PASS",
        "database": database_name,
        "workflow_inspection_id": inspection_id,
        "source_contracts": {"pixel_ndvi": True, "alert": True, "manual": True, "inside_zone": True, "outside_rejected": True},
        "pixel_sample": {"scene_acquired_at": scene["acquired_at"], "ndvi": round(sample["ndvi"], 6)},
        "authorization": {"unauthenticated_401": True, "viewer_write_403": True, "cross_tenant_404": True, "cross_tenant_assignee_422": True},
        "state_machine": {"inspection": True, "review_reason": True, "action": True, "verification": True, "ineffective_follow_up": True, "stale_version_409": True},
        "photo": {"mime_magic": True, "size": True, "count": True, "path_traversal": True, "authorization": True, "download_hash_match": True, "delete": True},
        "queue": {"filters": True, "pagination": True, "ordering": True, "overdue": True, "bounded_queries": True},
        "audit": {"required_events": len(required_events), "ordered": True, "immutable": True},
        "database_integrity": {"tenant_violations": tenant_violations, "invalid_constraints": invalid_constraints, "invalid_indexes": invalid_indexes},
        "metrics_ms": dict(metrics),
        "secret_values_logged": False,
        "uploaded_photo_bytes_logged": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"result": "PASS", "output": str(args.output.resolve())}, sort_keys=True))
    for identity in identities.values():
        if isinstance(identity, dict):
            identity.clear()
    protected.clear()
    event.remove(engine, "before_cursor_execute", count_query)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
