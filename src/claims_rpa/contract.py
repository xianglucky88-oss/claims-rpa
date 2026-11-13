"""Validation of adapter results before they reach the persistence layer."""

from __future__ import annotations

from typing import Any

from .errors import IdentityMismatch, InvalidResult, ProvenanceError

PORTAL_SOURCE = "portal"
SIMULATED_SOURCE = "simulated"
VALID_SOURCES = frozenset({PORTAL_SOURCE, SIMULATED_SOURCE})

REQUIRED_FIELDS = (
    "platform",
    "case_id",
    "status",
    "receipt_id",
    "source",
    "verified",
    "request_token",
    "page_platform",
    "page_case_id",
    "session_epoch",
)


def validate_query_result(result: Any, platform: str, case_id: str, *, dry_run: bool) -> dict[str, Any]:
    """Return a normalized result only if it is safe to persist.

    The result must be bound to the requested platform/case *and* carry the
    page evidence captured during this specific search.  A simulated result is
    accepted only in dry-run mode and is never marked as verified.
    """

    if not isinstance(result, dict):
        raise InvalidResult("adapter result must be a mapping")

    missing = [field for field in REQUIRED_FIELDS if field not in result]
    if missing:
        raise InvalidResult(f"adapter result is missing fields: {', '.join(missing)}")

    result_platform = str(result["platform"]).strip()
    result_case = str(result["case_id"]).strip()
    if not result_platform or not result_case:
        raise InvalidResult("adapter result platform and case_id must be non-empty")
    if result_platform != platform:
        raise IdentityMismatch(
            f"result platform {result_platform!r} does not match requested platform {platform!r}"
        )
    if result_case != case_id:
        raise IdentityMismatch(
            f"result case_id {result_case!r} does not match requested case_id {case_id!r}"
        )

    status = str(result["status"]).strip()
    if not status:
        raise InvalidResult("adapter result status must be non-empty")

    source = str(result["source"]).strip()
    if source not in VALID_SOURCES:
        raise InvalidResult(f"unknown result source {source!r}")
    if source == SIMULATED_SOURCE and not dry_run:
        raise ProvenanceError(
            "simulated result cannot be persisted as a real portal confirmation"
        )

    verified = result["verified"]
    if source == PORTAL_SOURCE:
        if verified is not True:
            raise InvalidResult("real portal result must be identity-verified")
    elif verified is not False:
        raise InvalidResult("simulated result must never be marked verified")

    page_platform = str(result["page_platform"]).strip()
    page_case_id = str(result["page_case_id"]).strip()
    request_token = str(result["request_token"]).strip()
    if not page_platform or not page_case_id:
        raise IdentityMismatch("result is missing page platform/case evidence")
    if not request_token:
        raise InvalidResult("result is missing the search token")
    if page_platform != platform or page_case_id != case_id:
        raise IdentityMismatch(
            "page evidence does not belong to the requested platform/case"
        )

    try:
        session_epoch = int(result["session_epoch"])
    except (TypeError, ValueError) as exc:
        raise InvalidResult("session_epoch must be an integer") from exc
    if session_epoch < 0:
        raise InvalidResult("session_epoch must be non-negative")

    return {
        "platform": result_platform,
        "case_id": result_case,
        "status": status,
        "receipt_id": str(result["receipt_id"]),
        "source": source,
        "verified": bool(verified),
        "request_token": request_token,
        "page_platform": page_platform,
        "page_case_id": page_case_id,
        "session_epoch": session_epoch,
    }
