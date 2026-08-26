"""Route definitions for floor-plan validation."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from knowledge_engine.apps.api.schemas import (
    ErrorResponse,
    FloorPlanRequest,
    ValidationResponse,
)
from knowledge_engine.apps.api.service import validate_floor_plan_payload

router = APIRouter(tags=["validation"])


@router.post(
    "/v1/validate",
    response_model=ValidationResponse,
    summary="Validate a floor plan",
    description=(
        "Accepts a JSON body containing a ``floor_plan`` object and runs the "
        "deterministic validation pipeline against it. All computation is "
        "local – no data leaves the machine."
    ),
    responses={
        200: {
            "description": "Validation completed successfully.",
            "model": ValidationResponse,
        },
        400: {
            "description": "Bad request – missing or malformed payload.",
            "model": ErrorResponse,
        },
        413: {
            "description": "Request body exceeds the 1 MB size limit.",
            "model": ErrorResponse,
        },
    },
)
async def post_validate(request: Request, body: FloorPlanRequest) -> ValidationResponse:
    """Validate an inline floor-plan payload and return the full report.

    The service function writes the floor-plan to a temporary file
    (required by the existing file-oriented pipeline) and cleans up
    immediately – no files persist after the request.
    """
    try:
        result = validate_floor_plan_payload(body.model_dump(mode="json"))
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_request", "message": str(exc)},
        )

    return ValidationResponse.model_validate(result)
