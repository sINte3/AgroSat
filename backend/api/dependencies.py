"""
Central API dependency module for AgroSat.
Provides role helpers, tenant scoping, and authorized field row access.
"""
from typing import Optional
from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db
from models.monitoring import User
from api.auth import get_current_active_user

ALLOWED_ROLES = {"admin", "manager", "agronomist", "viewer"}


def normalize_role(user: User) -> str:
    """Return the lowercased role string for a user."""
    return user.role.lower() if user.role else ""


def require_known_role(user: User = Depends(get_current_active_user)) -> str:
    """Require the user to have a recognized role. Returns the role string."""
    role = normalize_role(user)
    if role not in ALLOWED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unknown role"
        )
    return role


GLOBAL_ROLES = {"admin", "manager"}
TENANT_ROLES = {"agronomist", "viewer"}


def is_global_role(role: str) -> bool:
    return role in GLOBAL_ROLES


def is_tenant_role(role: str) -> bool:
    return role in TENANT_ROLES


def require_enterprise_scope(user: User = Depends(get_current_active_user)) -> Optional[int]:
    """Return enterprise_id for tenant-scoped users, None for global roles."""
    role = normalize_role(user)
    if role in GLOBAL_ROLES:
        return None  # global scope
    if role in TENANT_ROLES:
        if user.enterprise_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant user has no enterprise_id"
            )
        return user.enterprise_id
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Unknown role"
    )


def get_authorized_field_row(
    field_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    write: bool = False,
):
    """Fetch a field row with tenant-scoped access control.

    Uses explicit SQL with a tenant clause inside the query.
    For tenant-scoped users (agronomist, viewer), the SQL WHERE clause
    filters to the user's enterprise_id. Returns 404 for access to
    another enterprise's field (prevents ID harvesting).
    """
    role = normalize_role(current_user)
    if role not in ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="Unknown role")

    # Build query with optional tenant clause
    params = {"fid": field_id}
    tenant_clause = ""
    if is_tenant_role(role):
        if current_user.enterprise_id is None:
            raise HTTPException(status_code=403, detail="User has no enterprise_id")
        tenant_clause = " AND f.enterprise_id = :eid"
        params["eid"] = current_user.enterprise_id

    row = db.execute(
        text(f"""
            SELECT f.id, f.name, f.code, f.enterprise_id, f.area_ha,
                   f.centroid_lat, f.centroid_lon, f.irrigation_type,
                   f.soil_type, f.notes, f.is_active, f.created_at, f.updated_at,
                   ST_AsGeoJSON(f.geometry)::json AS geometry
            FROM fields f
            WHERE f.id = :fid{tenant_clause}
        """),
        params,
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Field not found")

    return row


def get_authorized_field_row_for_write(
    field_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Wrapper: blocks viewers, then delegates to get_authorized_field_row."""
    role = normalize_role(current_user)
    if role == "viewer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Viewers cannot modify fields"
        )
    return get_authorized_field_row(
        field_id=field_id,
        db=db,
        current_user=current_user,
        write=True,
    )
