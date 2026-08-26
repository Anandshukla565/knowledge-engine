from __future__ import annotations

from copy import deepcopy
from typing import Any

from .planner_models import DraftPlanRequest
from .compact_templates import build_30x40_east_3bhk_template
from .room_program import complete_room_program
from .spatial_planner import create_canonical_spatial_plan


def generate_draft_project_state(
    request: DraftPlanRequest | dict[str, Any],
    *,
    seed: int | None = 0,
) -> dict[str, Any]:
    """Generate a provisional canonical plan from explicit requirements.

    This function does not approve a plan, write SQLite, or enable Vastu
    scoring. The result must pass the validation pipeline before presentation.
    """

    normalized = request if isinstance(request, DraftPlanRequest) else DraftPlanRequest.model_validate(request)
    template_brief = build_30x40_east_3bhk_template(normalized)
    result = create_canonical_spatial_plan(template_brief or normalized.to_project_brief(), seed=seed)
    if template_brief:
        # The supported template is a reproducible artifact. Keep audit
        # timestamps out of its content hash so repeated renders are identical.
        result.setdefault("generation_metadata", {})["timestamp"] = "deterministic_template_v1"
    if template_brief and template_brief.get("parking"):
        # Canonical room construction does not carry parking records forward.
        # The template parking bay is independently validated downstream.
        result["parking"] = deepcopy(template_brief["parking"])
    result, program_blockers = complete_room_program(
        result,
        bathrooms=normalized.bathrooms,
        requires_parking=normalized.requires_parking,
        requires_pooja=normalized.requires_pooja,
    )
    room_types = [str(room.get("type", "")).lower() for room in result.get("rooms", [])]
    blockers: list[str] = []

    def add_blocker(message: str) -> None:
        if message not in blockers:
            blockers.append(message)

    for blocker in program_blockers:
        add_blocker(blocker)
    bedroom_count = sum("bedroom" in room_type for room_type in room_types)
    bathroom_count = sum("bathroom" in room_type or "toilet" in room_type for room_type in room_types)
    if bedroom_count < normalized.bhk:
        add_blocker(f"required bedrooms={normalized.bhk}, generated={bedroom_count}")
    if bathroom_count < normalized.bathrooms:
        add_blocker(f"required bathrooms={normalized.bathrooms}, generated={bathroom_count}")
    if normalized.requires_pooja and not any("pooja" in room_type for room_type in room_types):
        add_blocker("required pooja room was not generated")
    if normalized.requires_parking and not result.get("parking"):
        add_blocker("required parking was not generated")
    result["generation_request"] = normalized.model_dump(mode="json")
    result["template_id"] = (template_brief or {}).get("template_id")
    result["generation_status"] = "blocked" if blockers else "draft"
    result["generation_blockers"] = blockers
    if blockers:
        result["blocked_reason"] = "; ".join(blockers)
    return result
