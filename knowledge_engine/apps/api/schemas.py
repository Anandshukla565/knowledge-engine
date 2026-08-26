"""Pydantic v2 schemas for the Knowledge Engine loopback API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, JsonValue


# ---- Request schemas ----


class FloorPlanRequest(BaseModel):
    """Request body accepted by ``POST /v1/validate``.

    The schema mirrors the raw JSON object the legacy handler accepted under
    the ``floor_plan`` key, but lifted one level so callers send the floor-plan
    dict directly.
    """

    model_config = {"str_strip_whitespace": True}

    # ``floor_plan`` is intentionally JsonValue so callers can nest arbitrary
    # dicts/lists/numbers/strings without schema breakage – the validation
    # pipeline performs its own structural checks.
    floor_plan: JsonValue = Field(
        ...,
        description=(
            "Floor-plan data object (dict) passed directly to the "
            "validation pipeline. Structure is not constrained at the "
            "transport layer – schema validation is handled by the "
            "downstream pipeline."
        ),
    )


# ---- Response schemas ----


class HealthResponse(BaseModel):
    """Response from ``GET /health``."""

    status: str = Field(
        default="ok",
        description="Always 'ok' when the server is reachable.",
    )
    api_scope: str = Field(
        description="Scope of the API – always 'local_only'.",
    )
    operations: list[str] = Field(
        description="List of operations this API supports.",
    )
    accepts_file_paths: bool = Field(
        description="Whether the API accepts filesystem paths (always False).",
    )
    writes_sqlite: bool = Field(
        description="Whether the API writes to SQLite (always False).",
    )
    official_scoring_enabled: bool = Field(
        description="Whether official Vaastu scoring is enabled (always False).",
    )
    vastu_score: JsonValue | None = Field(
        default=None,
        description="Current vastu score, if scoring is enabled.",
    )
    scoring_gate_reason: str = Field(
        description="Human-readable explanation of the scoring gate status.",
    )


class CapabilitiesResponse(BaseModel):
    """Response from ``GET /v1/capabilities``."""

    api_scope: str = Field(
        description="Scope of the API – always 'local_only_loopback'.",
    )
    operations: list[str] = Field(
        description="Operations exposed by the API.",
    )
    accepts_file_paths: bool = Field(
        description="Whether callers may provide filesystem paths.",
    )
    writes_sqlite: bool = Field(
        description="Whether the server writes to a SQLite database.",
    )
    official_scoring_enabled: bool = Field(
        description="Whether the official (non-local) scoring path is active.",
    )
    vastu_score: JsonValue | None = Field(
        default=None,
        description="Vastu score when official scoring is enabled.",
    )
    scoring_gate_reason: str = Field(
        description="Reason the scoring gate is open or closed.",
    )


class ValidationResponse(BaseModel):
    """Response from ``POST /v1/validate``."""

    api_scope: str = Field(
        description="Always 'local_only' – this endpoint never exposes data off-device.",
    )
    validation_report: dict[str, Any] = Field(
        description="Full validation report as returned by the pipeline.",
    )
    scorecard: dict[str, Any] = Field(
        description="Scorecard model as returned by the pipeline.",
    )
    report_markdown: str = Field(
        description="Human-readable markdown rendering of the validation report.",
    )
    plan_svg: str = Field(
        description="SVG string visualising the floor plan with validation overlay.",
    )
    official_scoring_enabled: bool = Field(
        description="Official scoring flag propagated from the pipeline context.",
    )
    official_vastu_scoring_used: bool = Field(
        description="Whether the pipeline used the official Vaastu scorer.",
    )
    candidate_notes_used_for_scoring: bool = Field(
        description="Whether candidate-notes heuristics contributed to scoring.",
    )
    vastu_score: JsonValue | None = Field(
        default=None,
        description="Composite vastu score, if scoring ran.",
    )


class ErrorResponse(BaseModel):
    """Standardised error envelope."""

    error: str = Field(
        description="Machine-readable error code.",
    )
    message: str | None = Field(
        default=None,
        description="Human-readable detail, when available.",
    )
