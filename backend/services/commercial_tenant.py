"""Tenant-scoped commercial configuration with no payment or secret exposure."""

from datetime import datetime, timezone
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from api.dependencies import ALLOWED_ROLES, TENANT_ROLES


def tenant_namespaces(enterprise_id: int) -> dict[str, str]:
    value = int(enterprise_id)
    if value <= 0:
        raise ValueError("enterprise_id must be positive")
    return {
        "storage": f"tenants/{value}/",
        "cache": f"agrosat:tenant:{value}:",
        "jobs": f"tenant.{value}.",
        "exports": f"tenant-{value}/",
    }


def _one(result):
    if hasattr(result, "mappings"):
        return result.mappings().first()
    row = result.fetchone()
    return row._mapping if row is not None and hasattr(row, "_mapping") else row


def _all(result):
    if hasattr(result, "mappings"):
        return list(result.mappings().all())
    return [
        row._mapping if hasattr(row, "_mapping") else row
        for row in result.fetchall()
    ]


def _actor(user, enterprise_id: int, *, admin=False, management=False):
    role = str(getattr(user.role, "value", user.role) or "").strip().lower()
    if role not in ALLOWED_ROLES:
        raise HTTPException(403, "Unknown role")
    assigned = getattr(user, "enterprise_id", None)
    if role in TENANT_ROLES:
        if assigned is None:
            raise HTTPException(403, "User has no enterprise_id")
        if int(assigned) != int(enterprise_id):
            raise HTTPException(404, "Enterprise not found")
    if admin and role != "admin":
        raise HTTPException(403, "Administrator role required")
    if management and role not in {"admin", "manager"}:
        raise HTTPException(403, "Management role required")
    return role, int(user.id)


def _enterprise(db, enterprise_id):
    row = _one(db.execute(
        text(
            "SELECT id,name,code,is_active FROM enterprises "
            "WHERE id=:enterprise_id"
        ),
        {"enterprise_id": enterprise_id},
    ))
    if not row:
        raise HTTPException(404, "Enterprise not found")
    return row


def _json(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _audit(db, enterprise_id, actor_id, action, target_type, target_id, details):
    correlation = hashlib.sha256(
        f"{enterprise_id}:{actor_id}:{action}:{target_type}:{target_id}:"
        f"{datetime.now(timezone.utc).isoformat()}".encode()
    ).hexdigest()[:64]
    db.execute(
        text(
            """
            INSERT INTO tenant_commercial_audit_events
              (enterprise_id,actor_id,action,target_type,target_id,
               correlation_id,details)
            VALUES
              (:enterprise_id,:actor_id,:action,:target_type,:target_id,
               :correlation_id,CAST(:details AS jsonb))
            """
        ),
        {
            "enterprise_id": enterprise_id,
            "actor_id": actor_id,
            "action": action,
            "target_type": target_type,
            "target_id": str(target_id),
            "correlation_id": correlation,
            "details": _json(details),
        },
    )


def get_boundary(db, user, enterprise_id):
    _actor(user, enterprise_id)
    enterprise = _enterprise(db, enterprise_id)
    profile = _one(db.execute(
        text(
            """
            SELECT plan_code,subscription_state,feature_flags,quota_limits,
                   retention_policy,branding,created_at,updated_at
            FROM enterprise_commercial_profiles
            WHERE enterprise_id=:enterprise_id
            """
        ),
        {"enterprise_id": enterprise_id},
    ))
    providers = _all(db.execute(
        text(
            """
            SELECT provider_code,status,last_validated_at,last_failure_category
            FROM tenant_provider_credentials
            WHERE enterprise_id=:enterprise_id
            ORDER BY provider_code LIMIT 100
            """
        ),
        {"enterprise_id": enterprise_id},
    ))
    return {
        "enterprise": dict(enterprise),
        "profile": dict(profile) if profile else {
            "plan_code": "unconfigured",
            "subscription_state": "unsupported",
            "feature_flags": {},
            "quota_limits": {},
            "retention_policy": {},
            "branding": {},
            "created_at": None,
            "updated_at": None,
        },
        "namespaces": tenant_namespaces(enterprise_id),
        "providers": [
            {
                "provider_code": row["provider_code"],
                "configured": row["status"] in {"configured", "valid", "invalid"},
                "status": row["status"],
                "last_validated_at": row["last_validated_at"],
                "last_failure_category": row["last_failure_category"],
            }
            for row in providers
        ],
        "billing": {
            "supported": False,
            "state": profile["subscription_state"] if profile else "unsupported",
            "payment_processing": False,
        },
    }


def update_profile(db, user, enterprise_id, payload):
    _, actor_id = _actor(user, enterprise_id, admin=True)
    _enterprise(db, enterprise_id)
    namespaces = tenant_namespaces(enterprise_id)
    db.execute(
        text(
            """
            INSERT INTO enterprise_commercial_profiles
              (enterprise_id,plan_code,subscription_state,feature_flags,
               quota_limits,retention_policy,branding,namespaces,updated_by_id)
            VALUES
              (:enterprise_id,:plan_code,:subscription_state,
               CAST(:feature_flags AS jsonb),CAST(:quota_limits AS jsonb),
               CAST(:retention_policy AS jsonb),CAST(:branding AS jsonb),
               CAST(:namespaces AS jsonb),:actor_id)
            ON CONFLICT (enterprise_id) DO UPDATE SET
              plan_code=EXCLUDED.plan_code,
              subscription_state=EXCLUDED.subscription_state,
              feature_flags=EXCLUDED.feature_flags,
              quota_limits=EXCLUDED.quota_limits,
              retention_policy=EXCLUDED.retention_policy,
              branding=EXCLUDED.branding,
              namespaces=EXCLUDED.namespaces,
              updated_by_id=EXCLUDED.updated_by_id,
              updated_at=now()
            """
        ),
        {
            "enterprise_id": enterprise_id,
            "plan_code": payload.plan_code,
            "subscription_state": payload.subscription_state.value,
            "feature_flags": _json(payload.feature_flags),
            "quota_limits": _json(payload.quota_limits),
            "retention_policy": _json(payload.retention_policy),
            "branding": _json(payload.branding),
            "namespaces": _json(namespaces),
            "actor_id": actor_id,
        },
    )
    _audit(
        db, enterprise_id, actor_id, "profile.updated",
        "commercial_profile", enterprise_id,
        {"plan_code": payload.plan_code, "subscription_state": payload.subscription_state.value},
    )
    db.commit()
    return get_boundary(db, user, enterprise_id)


def list_memberships(db, user, enterprise_id, limit, offset):
    _actor(user, enterprise_id, management=True)
    _enterprise(db, enterprise_id)
    rows = _all(db.execute(
        text(
            """
            SELECT m.id,m.user_id,m.membership_role,m.status,m.created_at,
                   u.full_name,u.is_active
            FROM enterprise_memberships m
            JOIN users u ON u.id=m.user_id
            WHERE m.enterprise_id=:enterprise_id
            ORDER BY m.created_at DESC,m.id DESC LIMIT :limit OFFSET :offset
            """
        ),
        {"enterprise_id": enterprise_id, "limit": limit, "offset": offset},
    ))
    return {"limit": limit, "offset": offset, "items": [dict(row) for row in rows]}


def configure_provider(db, user, enterprise_id, provider_code, payload):
    _, actor_id = _actor(user, enterprise_id, admin=True)
    _enterprise(db, enterprise_id)
    db.execute(
        text(
            """
            INSERT INTO tenant_provider_credentials
              (enterprise_id,provider_code,secret_reference,status,updated_by_id)
            VALUES
              (:enterprise_id,:provider_code,:secret_reference,:status,:actor_id)
            ON CONFLICT (enterprise_id,provider_code) DO UPDATE SET
              secret_reference=EXCLUDED.secret_reference,
              status=EXCLUDED.status,updated_by_id=EXCLUDED.updated_by_id,
              updated_at=now()
            """
        ),
        {
            "enterprise_id": enterprise_id,
            "provider_code": provider_code,
            "secret_reference": payload.secret_reference,
            "status": payload.status,
            "actor_id": actor_id,
        },
    )
    _audit(
        db, enterprise_id, actor_id, "provider.configured",
        "provider", provider_code, {"status": payload.status},
    )
    db.commit()
    return {"provider_code": provider_code, "configured": payload.status == "configured", "status": payload.status}


def _request_fingerprint(actor_id, payload):
    return hashlib.sha256(_json({
        "actor_id": actor_id,
        "payload": payload.model_dump(mode="json"),
    }).encode()).hexdigest()


def create_lifecycle_request(db, user, enterprise_id, payload, key):
    role, actor_id = _actor(user, enterprise_id, management=True)
    if role == "manager" and payload.request_type.value != "export":
        raise HTTPException(403, "Managers may request tenant export only")
    _enterprise(db, enterprise_id)
    fingerprint = _request_fingerprint(actor_id, payload)
    try:
        existing = _one(db.execute(
            text(
                """
                SELECT id,request_fingerprint FROM tenant_lifecycle_requests
                WHERE enterprise_id=:enterprise_id
                  AND requested_by_id=:actor_id AND client_request_id=:key
                """
            ),
            {"enterprise_id": enterprise_id, "actor_id": actor_id, "key": key},
        ))
        if existing:
            if existing["request_fingerprint"] != fingerprint:
                raise HTTPException(409, "Idempotency key payload conflict")
            return False, get_lifecycle_request(
                db, user, enterprise_id, existing["id"]
            )
        inserted = _one(db.execute(
            text(
                """
                INSERT INTO tenant_lifecycle_requests
                  (enterprise_id,request_type,status,requested_by_id,reason,
                   client_request_id,request_fingerprint)
                VALUES
                  (:enterprise_id,:request_type,'requested',:actor_id,:reason,
                   :key,:fingerprint)
                RETURNING id
                """
            ),
            {
                "enterprise_id": enterprise_id,
                "request_type": payload.request_type.value,
                "actor_id": actor_id,
                "reason": payload.reason,
                "key": key,
                "fingerprint": fingerprint,
            },
        ))
        _audit(
            db, enterprise_id, actor_id, "lifecycle.requested",
            "lifecycle_request", inserted["id"],
            {"request_type": payload.request_type.value},
        )
        db.commit()
        return True, get_lifecycle_request(db, user, enterprise_id, inserted["id"])
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Concurrent lifecycle request conflict")


def get_lifecycle_request(db, user, enterprise_id, request_id):
    _actor(user, enterprise_id, management=True)
    row = _one(db.execute(
        text(
            """
            SELECT id,enterprise_id,request_type,status,requested_by_id,
                   reviewed_by_id,reviewed_at,reason,decision_note,
                   created_at,updated_at,result_reference,failure_category
            FROM tenant_lifecycle_requests
            WHERE id=:request_id AND enterprise_id=:enterprise_id
            """
        ),
        {"request_id": request_id, "enterprise_id": enterprise_id},
    ))
    if not row:
        raise HTTPException(404, "Lifecycle request not found")
    return dict(row)


def list_lifecycle_requests(db, user, enterprise_id, limit, offset):
    _actor(user, enterprise_id, management=True)
    _enterprise(db, enterprise_id)
    rows = _all(db.execute(
        text(
            """
            SELECT id,enterprise_id,request_type,status,requested_by_id,
                   reviewed_by_id,reviewed_at,reason,decision_note,
                   created_at,updated_at,result_reference,failure_category
            FROM tenant_lifecycle_requests
            WHERE enterprise_id=:enterprise_id
            ORDER BY created_at DESC,id DESC LIMIT :limit OFFSET :offset
            """
        ),
        {"enterprise_id": enterprise_id, "limit": limit, "offset": offset},
    ))
    return {"limit": limit, "offset": offset, "items": [dict(row) for row in rows]}


def decide_lifecycle_request(db, user, enterprise_id, request_id, payload):
    _, actor_id = _actor(user, enterprise_id, admin=True)
    now = datetime.now(timezone.utc)
    updated = _one(db.execute(
        text(
            """
            UPDATE tenant_lifecycle_requests
            SET status=:decision,reviewed_by_id=:actor_id,reviewed_at=:now,
                decision_note=:note,updated_at=:now
            WHERE id=:request_id AND enterprise_id=:enterprise_id
              AND status='requested'
            RETURNING id
            """
        ),
        {
            "decision": payload.decision,
            "actor_id": actor_id,
            "now": now,
            "note": payload.note,
            "request_id": request_id,
            "enterprise_id": enterprise_id,
        },
    ))
    if not updated:
        existing = _one(db.execute(
            text(
                "SELECT id,status FROM tenant_lifecycle_requests "
                "WHERE id=:request_id AND enterprise_id=:enterprise_id"
            ),
            {"request_id": request_id, "enterprise_id": enterprise_id},
        ))
        if not existing:
            raise HTTPException(404, "Lifecycle request not found")
        raise HTTPException(409, "Lifecycle request is no longer pending")
    _audit(
        db, enterprise_id, actor_id, f"lifecycle.{payload.decision}",
        "lifecycle_request", request_id, {"decision": payload.decision},
    )
    db.commit()
    return get_lifecycle_request(db, user, enterprise_id, request_id)
