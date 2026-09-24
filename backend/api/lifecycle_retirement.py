"""HTTP 410 contract for write endpoints retired by TASK_225.

AgroSat has one inspection lifecycle (TASK_217, /api/anomaly-inspections) and
one remediation lifecycle (TASK_220, /api/agronomy-plans). The TASK_209
inspection, corrective-action and verification-request writes, the TASK_217
corrective-action writes and the web-process NDVI refresh are retired: they
answer 410 Gone with the canonical replacement and never touch the database.
Their read endpoints stay, so historical rows remain readable.

Retired routes still authenticate first, so an anonymous caller learns nothing
beyond what any unauthenticated request learns.
"""

from fastapi import HTTPException, status


RETIRED_CODE = "lifecycle_endpoint_retired"


def retired(endpoint: str, replacement: str, message: str) -> HTTPException:
    """Build the 410 raised by a retired endpoint; no side effect happens first."""
    return HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": RETIRED_CODE,
            "endpoint": endpoint,
            "replacement": replacement,
            "message": message,
        },
    )
