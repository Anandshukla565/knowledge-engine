"""Deterministic tools that an AI agent may call through the local runtime."""

from __future__ import annotations

from typing import Any

from knowledge_engine.apps.api.service import api_capabilities, validate_floor_plan_payload


def get_tool_capabilities() -> dict[str, Any]:
    """Describe the small, local-only tool surface without invoking an LLM."""
    return api_capabilities()


def validate_floor_plan(floor_plan: dict[str, Any]) -> dict[str, Any]:
    """Run deterministic validation for an inline floor-plan object."""
    return validate_floor_plan_payload({"floor_plan": floor_plan})
