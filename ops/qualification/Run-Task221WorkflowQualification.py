#!/usr/bin/env python3
"""Real PostgreSQL-backed Operational Command Center qualification for TASK_221."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import event, text


PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDAT\x08\xd7c\xf8"
    b"\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND"
    b"\xaeB`\x82"
)


def require(response, status: int, label: str):
    if response.status_code != status:
        raise AssertionError(f"{label}: {response.status_code} {response.text[:500]}")
    return response.json() if response.content else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    backend = Path(__file__).resolve().parents[2] / "backend"
    sys.path.insert(0, str(backend))

    from api.auth import get_current_active_user
    from database import SessionLocal, engine
    from main import app
    from services import operational_center
    from services.operational_notifications import reconcile_notifications

    database_name = str(engine.url.database)
    if database_name == "agrosat" or not database_name.startswith("agrosat_r3_task221_"):
        raise RuntimeError("TASK221_DATABASE_IDENTITY_REJECTED")
    if os.environ.get("WIALON_ENABLED", "false").lower() != "false":
        raise RuntimeError("TASK221_WIALON_MUST_REMAIN_DISABLED")
    if os.environ.get("TELEGRAM_NOTIFICATIONS_ENABLED", "false").lower() != "false":
        raise RuntimeError("TASK221_TELEGRAM_MUST_REMAIN_DISABLED")

    logging.getLogger("httpx").setLevel(logging.WARNING)
    tag = uuid.uuid4().hex
    today = date.today()
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        fields = connection.execute(
            text("SELECT f.id,f.enterprise_id FROM fields f ORDER BY f.id")
        ).mappings().all()
        first = fields[0]
        other = next(row for row in fields if row["enterprise_id"] != first["enterprise_id"])
        inside_geometry = connection.execute(
            text(
                "SELECT ST_AsGeoJSON(ST_PointOnSurface(geometry))::json "
                "FROM fields WHERE id=:id"
            ),
            {"id": first["id"]},
        ).scalar_one()
        password_hash = connection.execute(
            text("SELECT hashed_password FROM users ORDER BY id LIMIT 1")
        ).scalar_one()

        def create_user(email: str, role: str, enterprise_id: int | None) -> int:
            return connection.execute(
                text(
                    "INSERT INTO users(enterprise_id,email,full_name,role,hashed_password," 
                    "is_active,created_at) VALUES(:enterprise,:email,:name,:role,:hash,true,now()) "
                    "RETURNING id"
                ),
                {
                    "enterprise": enterprise_id,
                    "email": email,
                    "name": "TASK 221 qualification",
                    "role": role,
                    "hash": password_hash,
                },
            ).scalar_one()

        admin = create_user(f"task221-admin-{tag}@invalid.example", "admin", None)
        manager = create_user(
            f"task221-manager-{tag}@invalid.example", "manager", first["enterprise_id"]
        )
        agronomist = create_user(
            f"task221-agronomist-{tag}@invalid.example",
            "agronomist",
            first["enterprise_id"],
        )
        viewer = create_user(
            f"task221-viewer-{tag}@invalid.example", "viewer", first["enterprise_id"]
        )
        cross_manager = create_user(
            f"task221-cross-{tag}@invalid.example", "manager", other["enterprise_id"]
        )
        connection.execute(
            text(
                "INSERT INTO ndvi_records(field_id,captured_date,processed_at,mean_ndvi," 
                "min_ndvi,max_ndvi,std_ndvi,p10_ndvi,p90_ndvi,cloud_cover_pct," 
                "valid_pixels_pct,satellite) VALUES(:field,:day,now(),.40,.2,.6,.1,.3,.5,5,95," 
                "'Sentinel-2') ON CONFLICT(field_id,captured_date) DO UPDATE SET " 
                "mean_ndvi=.40,cloud_cover_pct=5,valid_pixels_pct=95"
            ),
            {"field": first["id"], "day": today - timedelta(days=15)},
        )
        inspection = connection.execute(
            text(
                "INSERT INTO field_inspections(field_id,enterprise_id,created_by_id," 
                "assigned_to_id,client_request_id,request_fingerprint,source,source_kind," 
                "source_reason_codes,title,instructions,status,version,created_at,updated_at," 
                "source_reason,priority,submitted_at,source_snapshot_locked) VALUES(" 
                ":field,:enterprise,:admin,:agronomist,:key,:fingerprint,'manual','manual'," 
                "'[]'::jsonb,'TASK 221 linked inspection','Qualified field finding','submitted'," 
                "1,now(),now(),'Operational center qualification','high',now(),true) RETURNING id"
            ),
            {
                "field": first["id"],
                "enterprise": first["enterprise_id"],
                "admin": admin,
                "agronomist": agronomist,
                "key": f"task221-linked-{tag}",
                "fingerprint": "a" * 64,
            },
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO inspection_results(inspection_id,field_id,enterprise_id," 
                "recorded_by_id,cause_code,severity,affected_area_ha,observations," 
                "recommended_action,sync_state,version,created_at,updated_at) VALUES(" 
                ":inspection,:field,:enterprise,:user,'nutrient_deficiency','high',1.2," 
                "'Symptoms recorded','Collect samples before treatment','server',1,now(),now())"
            ),
            {
                "inspection": inspection,
                "field": first["id"],
                "enterprise": first["enterprise_id"],
                "user": agronomist,
            },
        )
        overdue_inspection = connection.execute(
            text(
                "INSERT INTO field_inspections(field_id,enterprise_id,created_by_id," 
                "assigned_to_id,client_request_id,request_fingerprint,source,source_kind," 
                "source_reason_codes,title,instructions,status,version,created_at,updated_at," 
                "source_reason,priority,due_at,source_snapshot_locked) VALUES(" 
                ":field,:enterprise,:admin,:agronomist,:key,:fingerprint,'manual','manual'," 
                "'[]'::jsonb,'TASK 221 overdue critical inspection','Inspect urgently'," 
                "'assigned',1,now(),now(),'Overdue qualification','urgent',:due,false) RETURNING id"
            ),
            {
                "field": first["id"],
                "enterprise": first["enterprise_id"],
                "admin": admin,
                "agronomist": agronomist,
                "key": f"task221-overdue-{tag}",
                "fingerprint": "b" * 64,
                "due": now - timedelta(hours=2),
            },
        ).scalar_one()

    actors = {
        "admin": SimpleNamespace(id=admin, role="admin", enterprise_id=None),
        "manager": SimpleNamespace(
            id=manager, role="manager", enterprise_id=first["enterprise_id"]
        ),
        "agronomist": SimpleNamespace(
            id=agronomist, role="agronomist", enterprise_id=first["enterprise_id"]
        ),
        "viewer": SimpleNamespace(
            id=viewer, role="viewer", enterprise_id=first["enterprise_id"]
        ),
        "cross": SimpleNamespace(
            id=cross_manager, role="manager", enterprise_id=other["enterprise_id"]
        ),
    }
    current = {"actor": actors["admin"]}
    app.dependency_overrides[get_current_active_user] = lambda: current["actor"]
    original_weather = operational_center.detail.__kwdefaults__["weather_loader"]
    operational_center.detail.__kwdefaults__["weather_loader"] = lambda _lat, _lon: None
    client = TestClient(app)
    api_calls = 0

    def call(method: str, url: str, **kwargs):
        nonlocal api_calls
        api_calls += 1
        return client.request(method, url, **kwargs)

    try:
        draft = require(
            call(
                "POST",
                "/api/agronomy-plans",
                json={
                    "inspection_id": inspection,
                    "reason": "Create TASK 221 linked agronomy plan",
                },
                headers={"Idempotency-Key": f"task221-plan-{tag}"},
            ),
            201,
            "create plan",
        )
        plan_id = draft["plan_id"]
        detail = require(call("GET", f"/api/agronomy-plans/{plan_id}"), 200, "plan detail")
        due_at = (now + timedelta(days=2)).isoformat()
        added = require(
            call(
                "POST",
                f"/api/agronomy-plans/{plan_id}/work",
                json={
                    "category": "sampling",
                    "instruction": "Collect samples and preserve execution evidence",
                    "assigned_to_id": agronomist,
                    "planned_start_at": None,
                    "due_at": due_at,
                    "geometry": inside_geometry,
                    "expected_version": detail["version"],
                    "reason": "Qualified operational work",
                },
                headers={"Idempotency-Key": f"task221-work-{tag}"},
            ),
            201,
            "add work",
        )
        item_id = added["item_id"]
        approved = require(
            call(
                "POST",
                f"/api/agronomy-plans/{plan_id}/transition",
                json={
                    "operation": "approve",
                    "expected_version": added["version"],
                    "reason": "Manager approved scope and accountability",
                },
                headers={"Idempotency-Key": f"task221-approve-{tag}"},
            ),
            200,
            "approve",
        )
        current["actor"] = actors["agronomist"]
        started = require(
            call(
                "POST",
                f"/api/agronomy-plans/{plan_id}/work/{item_id}/transition",
                json={
                    "operation": "start",
                    "expected_plan_version": approved["version"],
                    "expected_version": added["item_version"],
                    "reason": "Assigned agronomist started work",
                    "assigned_to_id": agronomist,
                    "due_at": due_at,
                    "planned_start_at": None,
                    "result_note": None,
                },
                headers={"Idempotency-Key": f"task221-start-{tag}"},
            ),
            200,
            "start",
        )
        evidence = require(
            call(
                "POST",
                f"/api/agronomy-plans/{plan_id}/work/{item_id}/evidence",
                data={
                    "expected_plan_version": started["version"],
                    "expected_version": started["item_version"],
                    "key": f"task221-evidence-{tag}",
                },
                files={"photo": ("evidence.png", PNG, "image/png")},
            ),
            201,
            "evidence",
        )
        completed = require(
            call(
                "POST",
                f"/api/agronomy-plans/{plan_id}/work/{item_id}/transition",
                json={
                    "operation": "complete",
                    "expected_plan_version": evidence["version"],
                    "expected_version": evidence["item_version"],
                    "reason": "Execution and evidence completed",
                    "assigned_to_id": agronomist,
                    "due_at": due_at,
                    "planned_start_at": None,
                    "result_note": "Samples collected and evidence attached",
                },
                headers={"Idempotency-Key": f"task221-complete-{tag}"},
            ),
            200,
            "complete",
        )
        if completed["status"] != "pending_verification":
            raise AssertionError("completed work must await satellite verification")

        with SessionLocal() as session:
            first_reconcile = reconcile_notifications(session, apply=True, limit=500, as_of=now)
        with SessionLocal() as session:
            second_reconcile = reconcile_notifications(session, apply=True, limit=500, as_of=now)
        if first_reconcile["created"] < 1 or second_reconcile["created"] != 0:
            raise AssertionError("notification reconciliation is not idempotent")

        current["actor"] = actors["manager"]
        queue = require(
            call("GET", "/api/operational-center/queue?limit=100&offset=0"),
            200,
            "manager queue",
        )
        queue_by_key = {item["case_key"]: item for item in queue["items"]}
        linked_key = f"inspection:{inspection}"
        overdue_key = f"inspection:{overdue_inspection}"
        if linked_key not in queue_by_key or overdue_key not in queue_by_key:
            raise AssertionError("linked operational cases are absent from manager queue")
        if queue_by_key[linked_key]["operational_status"] != "awaiting_verification":
            raise AssertionError("closed-loop case did not derive awaiting_verification")
        if not queue_by_key[overdue_key]["is_overdue"]:
            raise AssertionError("overdue inspection did not derive overdue state")
        positions = {item["case_key"]: index for index, item in enumerate(queue["items"])}
        if positions[overdue_key] > positions[linked_key]:
            raise AssertionError("deterministic overdue priority ordering failed")

        summary = require(
            call("GET", "/api/operational-center/summary?limit=100&offset=0"),
            200,
            "summary",
        )
        if summary["overdue_work"] < 1 or summary["awaiting_satellite_verification"] < 1:
            raise AssertionError("summary did not count qualified states")
        case_detail = require(
            call("GET", f"/api/operational-center/cases/{linked_key}"),
            200,
            "case detail",
        )
        if (
            case_detail["weather"]["status"] != "unavailable"
            or case_detail["telematics"]["status"] != "unsupported"
            or not case_detail["evidence"]
            or not case_detail["timeline"]
        ):
            raise AssertionError("case evidence/degraded context contract failed")
        require(
            call("GET", f"/api/operational-center/fields/{first['id']}/timeline?limit=100"),
            200,
            "field timeline",
        )

        current["actor"] = actors["cross"]
        require(
            call("GET", f"/api/operational-center/cases/{linked_key}"),
            404,
            "cross tenant detail",
        )
        require(
            call(
                "GET",
                f"/api/operational-center/queue?enterprise_id={first['enterprise_id']}",
            ),
            404,
            "cross tenant enterprise filter",
        )
        current["actor"] = actors["viewer"]
        require(call("GET", "/api/operational-center/queue"), 200, "viewer read")
        require(
            call(
                "POST",
                "/api/operational-center/notifications/1/transition",
                json={"action": "read", "expected_version": 1},
                headers={"Idempotency-Key": f"task221-view-{tag}"},
            ),
            403,
            "viewer mutation",
        )
        current["actor"] = actors["agronomist"]
        agronomist_queue = require(
            call("GET", "/api/operational-center/queue?limit=100"),
            200,
            "agronomist queue",
        )
        if not agronomist_queue["items"] or any(
            item["assignee_id"] != agronomist for item in agronomist_queue["items"]
        ):
            raise AssertionError("agronomist assignment scope failed")

        current["actor"] = actors["manager"]
        notifications = require(
            call("GET", f"/api/operational-center/notifications?case_key={linked_key}"),
            200,
            "notifications",
        )
        notification = notifications["items"][0]
        transition_key = f"task221-read-{tag}"
        transition_payload = {
            "action": "read",
            "expected_version": notification["version"],
        }
        read = require(
            call(
                "POST",
                f"/api/operational-center/notifications/{notification['id']}/transition",
                json=transition_payload,
                headers={"Idempotency-Key": transition_key},
            ),
            200,
            "notification read",
        )
        replay = require(
            call(
                "POST",
                f"/api/operational-center/notifications/{notification['id']}/transition",
                json=transition_payload,
                headers={"Idempotency-Key": transition_key},
            ),
            200,
            "notification replay",
        )
        if read["replayed"] or not replay["replayed"]:
            raise AssertionError("notification transition replay contract failed")
        require(
            call(
                "POST",
                f"/api/operational-center/notifications/{notification['id']}/transition",
                json={
                    "action": "dismiss",
                    "expected_version": read["version"],
                    "reason": "Conflicting payload",
                },
                headers={"Idempotency-Key": transition_key},
            ),
            409,
            "notification key conflict",
        )

        query_counts: dict[str, int] = {}
        active_label = {"value": ""}

        def before_cursor_execute(*_args):
            label = active_label["value"]
            if label:
                query_counts[label] = query_counts.get(label, 0) + 1

        event.listen(engine, "before_cursor_execute", before_cursor_execute)
        try:
            for label, url, budget in (
                ("queue", "/api/operational-center/queue?limit=100", 3),
                ("summary", "/api/operational-center/summary?limit=100", 2),
                ("detail", f"/api/operational-center/cases/{linked_key}", 12),
                ("timeline", f"/api/operational-center/fields/{first['id']}/timeline", 3),
            ):
                active_label["value"] = label
                require(call("GET", url), 200, f"{label} query budget")
                active_label["value"] = ""
                if query_counts.get(label, 0) > budget:
                    raise AssertionError(
                        f"{label} query budget exceeded: {query_counts[label]} > {budget}"
                    )
        finally:
            active_label["value"] = ""
            event.remove(engine, "before_cursor_execute", before_cursor_execute)

        ready = require(call("GET", "/health/ready"), 200, "readiness")
        if ready["status"] != "ready":
            raise AssertionError("external degraded state changed API readiness")
        with engine.connect() as connection:
            duplicate_notifications = connection.execute(
                text(
                    "SELECT count(*) FROM (SELECT enterprise_id,dedupe_key,count(*) "
                    "FROM operational_notifications GROUP BY 1,2 HAVING count(*)>1) value"
                )
            ).scalar_one()
            duplicate_commands = connection.execute(
                text(
                    "SELECT count(*) FROM (SELECT actor_key,command_key,count(*) "
                    "FROM operational_notification_events GROUP BY 1,2 HAVING count(*)>1) value"
                )
            ).scalar_one()
            tenant_orphans = connection.execute(
                text(
                    "SELECT count(*) FROM operational_notifications n LEFT JOIN fields f "
                    "ON f.id=n.field_id AND f.enterprise_id=n.enterprise_id "
                    "WHERE n.field_id IS NOT NULL AND f.id IS NULL"
                )
            ).scalar_one()
        if duplicate_notifications or duplicate_commands or tenant_orphans:
            raise AssertionError("notification identity or tenant integrity failed")

        downgrade = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                "alembic.ini",
                "downgrade",
                "0015_closed_loop_agronomy",
            ],
            cwd=backend,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if downgrade.returncode == 0:
            raise AssertionError("populated operational history downgrade must fail closed")
        with engine.connect() as connection:
            revision_after_failed_downgrade = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        if revision_after_failed_downgrade != "0016_operational_command_center":
            raise AssertionError("failed downgrade changed the schema revision")

        result = {
            "status": "PASS",
            "database": database_name,
            "linked_case_key": linked_key,
            "overdue_case_key": overdue_key,
            "plan_id": plan_id,
            "work_item_id": item_id,
            "evidence_count": len(case_detail["evidence"]),
            "operational_states": ["overdue", "awaiting_verification"],
            "weather_status": "unavailable",
            "telematics_status": "unsupported",
            "wialon_enabled": False,
            "telegram_enabled": False,
            "first_reconcile": first_reconcile,
            "second_reconcile": second_reconcile,
            "notification_transition_replayed": True,
            "cross_tenant_non_enumeration": 2,
            "viewer_write_denials": 1,
            "query_counts": query_counts,
            "query_budgets": {"queue": 3, "summary": 2, "detail": 12, "timeline": 3},
            "duplicate_notification_identities": duplicate_notifications,
            "duplicate_command_identities": duplicate_commands,
            "tenant_orphans": tenant_orphans,
            "populated_downgrade_failed_closed": True,
            "revision_after_failed_downgrade": revision_after_failed_downgrade,
            "api_calls": api_calls,
            "production_data_touched": False,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))
        return 0
    finally:
        operational_center.detail.__kwdefaults__["weather_loader"] = original_weather
        app.dependency_overrides.clear()
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
