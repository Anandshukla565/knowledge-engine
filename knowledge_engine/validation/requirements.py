from __future__ import annotations

from knowledge_engine.domain.findings import ValidationIssue
from knowledge_engine.domain.floor_plan import FloorPlanSchema

BEDROOM_TYPES = {"bedroom", "master_bedroom"}


def validate_requirements(plan: FloorPlanSchema) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    bedroom_count = sum(1 for room in plan.rooms if room.type in BEDROOM_TYPES)
    required_bedrooms = plan.requirements.required_bedrooms_count or 0
    if bedroom_count < required_bedrooms:
        issues.append(
            ValidationIssue(
                severity="error",
                code="missing_required_bedrooms",
                message=(
                    f"Plan provides {bedroom_count} bedroom spaces but requires {required_bedrooms}."
                ),
                location="requirements.required_bedrooms_count",
            )
        )
    if plan.requirements.requires_parking and not plan.parking:
        issues.append(
            ValidationIssue(
                severity="error",
                code="missing_required_parking",
                message="Parking is required but no parking rectangle is present.",
                location="parking",
            )
        )
    if plan.requirements.requires_pooja and not any(room.type == "pooja" for room in plan.rooms):
        issues.append(
            ValidationIssue(
                severity="error",
                code="missing_required_pooja",
                message="Pooja is required but no pooja room is present.",
                location="rooms",
            )
        )
    return issues
