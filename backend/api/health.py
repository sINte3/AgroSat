"""Public health endpoints with sanitized dependency state."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from services.health import current_readiness_snapshot, liveness_snapshot


router = APIRouter(tags=["health"])


@router.get("/health/live")
def liveness():
    return liveness_snapshot()


@router.get("/health/ready")
def readiness():
    snapshot = current_readiness_snapshot()
    return JSONResponse(
        status_code=200 if snapshot["status"] == "ready" else 503,
        content=snapshot,
    )


@router.get("/health")
def compatibility_health():
    snapshot = current_readiness_snapshot()
    return {
        **snapshot,
        "status": "ok" if snapshot["status"] == "ready" else "degraded",
    }
