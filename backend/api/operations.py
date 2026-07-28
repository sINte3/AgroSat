"""Authenticated, low-cardinality operational metrics."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from api.auth import get_current_active_user
from api.dependencies import normalize_role
from config import settings
from services.health import collector_readiness
from services.metrics import REGISTRY, collector_metrics


router = APIRouter(prefix="/api/operations", tags=["operations"])


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(current_user=Depends(get_current_active_user)):
    if normalize_role(current_user) not in {"admin", "manager"}:
        raise HTTPException(status_code=403, detail="Management role required")
    collector = collector_readiness(
        settings.collector_status_directory,
        settings.collector_stale_after_seconds,
    )
    return PlainTextResponse(
        REGISTRY.render() + collector_metrics(collector),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
