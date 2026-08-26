"""Route definitions for the Knowledge Engine loopback API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from knowledge_engine.apps.api.schemas import (
    CapabilitiesResponse,
    ErrorResponse,
    HealthResponse,
)
from knowledge_engine.apps.api.service import api_capabilities

router = APIRouter(tags=["meta"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description=(
        "Returns a 200 OK payload confirming the server is running and "
        "includes the API capabilities metadata for convenience."
    ),
    responses={
        200: {
            "description": "Server is healthy and reachable.",
            "content": {"application/json": {"example": {"status": "ok"}}},
        }
    },
)
def get_health(request: Request) -> HealthResponse:
    """Health-check endpoint – always returns 200 when the server is up."""
    capabilities = api_capabilities()
    return HealthResponse.model_validate({"status": "ok", **capabilities})


@router.get(
    "/v1/capabilities",
    response_model=CapabilitiesResponse,
    summary="API capabilities",
    description=(
        "Describes what the API can do, which scoring features are active, "
        "and whether the server writes to the filesystem or databases."
    ),
    responses={
        200: {
            "description": "Capabilities descriptor returned successfully.",
        }
    },
)
def get_capabilities() -> CapabilitiesResponse:
    """Return the server's capability descriptor."""
    return CapabilitiesResponse.model_validate(api_capabilities())


@router.get(
    "/",
    include_in_schema=False,
    summary="Root redirect hint",
    description="Points callers toward the health and capability endpoints.",
)
def root_index() -> JSONResponse:
    """Simple root – nothing meaningful lives here; see /health."""
    return JSONResponse(
        status_code=200,
        content={
            "message": "Knowledge Engine local API",
            "endpoints": ["/health", "/v1/capabilities", "/v1/validate"],
        },
    )
