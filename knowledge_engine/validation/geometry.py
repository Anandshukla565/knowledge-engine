from __future__ import annotations

from typing import Any

from knowledge_engine.domain.findings import ValidationIssue
from knowledge_engine.domain.floor_plan import FloorPlanSchema, RoomSchema
from knowledge_engine.validation.rectangles import Rect, rectangles_overlap
from knowledge_engine.validation.zones import brahmasthan_bounds, classify_room_zone

SEVERITY_ORDER = {"OK": 0, "WARNING": 1, "NEEDS_REVIEW": 2, "HIGH_RISK": 3}
OK_ROOM_TYPES = {"living", "dining", "circulation"}
POSITIVE_CENTER_KEYWORDS = {
    "open courtyard",
    "courtyard",
    "open living",
    "open living area",
    "open dining",
    "dining",
    "circulation",
    "atrium",
    "light well",
    "lightwell",
    "family space",
    "family area",
    "double height",
}
REVIEW_KEYWORDS = {"stair", "staircase", "shaft", "duct", "column", "structural"}
WET_SERVICE_KEYWORDS = {"toilet", "bathroom", "bath", "septic", "wet shaft", "wet service"}
HEAVY_STORAGE_KEYWORDS = {"heavy storage", "storage room", "store room"}
WET_ROOM_TYPES = {"toilet", "bathroom"}
SENSITIVE_STACK_ROOM_TYPES = {"kitchen", "pooja", "bedroom", "master_bedroom", "living"}
HEAVY_STACK_ROOM_TYPES = {"staircase"}


def _plot_rect(plan: FloorPlanSchema) -> Rect:
    return Rect(x=0.0, y=0.0, width=plan.plot.width_ft, height=plan.plot.depth_ft)


def _room_rectangles(plan: FloorPlanSchema) -> dict[str, Rect]:
    return {
        room.id: Rect(x=room.x, y=room.y, width=room.width, height=room.height)
        for room in plan.rooms
    }


def _normalized_text(*values: Any) -> str:
    parts: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            parts.append(value.strip().lower())
        elif isinstance(value, list):
            parts.extend(str(item).strip().lower() for item in value if item is not None)
        else:
            parts.append(str(value).strip().lower())
    return " ".join(part for part in parts if part)


def _contains_keyword(text: str, keywords: set[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _overlap_area(rect_a: Rect, rect_b: Rect) -> float:
    overlap_width = max(0.0, min(rect_a.right, rect_b.right) - max(rect_a.left, rect_b.left))
    overlap_height = max(0.0, min(rect_a.top, rect_b.top) - max(rect_a.bottom, rect_b.bottom))
    return round(overlap_width * overlap_height, 2)


def _intersection_rect(rect_a: Rect, rect_b: Rect) -> Rect | None:
    left = max(rect_a.left, rect_b.left)
    right = min(rect_a.right, rect_b.right)
    bottom = max(rect_a.bottom, rect_b.bottom)
    top = min(rect_a.top, rect_b.top)
    if left >= right or bottom >= top:
        return None
    return Rect(x=left, y=bottom, width=right - left, height=top - bottom)


def _positive_treatment_present(plan: FloorPlanSchema) -> bool:
    treatment = plan.brahmasthan_treatment
    if treatment is None:
        return False
    if treatment.open_to_sky or treatment.double_height or treatment.light_well:
        return True
    return _contains_keyword(_normalized_text(treatment.type, treatment.notes), POSITIVE_CENTER_KEYWORDS)


def _apply_plot_context(status: str, reason: str, plan: FloorPlanSchema) -> tuple[str, str]:
    if status == "HIGH_RISK":
        return status, reason
    if plan.plot.small_plot_context or plan.plot.multi_floor_context:
        if status in {"WARNING", "NEEDS_REVIEW"}:
            return (
                "NEEDS_REVIEW",
                reason + " Small-plot or multi-floor context adds ambiguity and requires manual review.",
            )
    return status, reason


def _classify_room_overlap(room: RoomSchema, plan: FloorPlanSchema) -> tuple[str, str]:
    room_text = _normalized_text(room.type, room.name, room.center_treatment, room.notes)
    if room.is_wet_service or room.type in {"toilet", "bathroom"} or _contains_keyword(room_text, WET_SERVICE_KEYWORDS):
        return "HIGH_RISK", "Wet-service overlap at the center zone should be treated as a high-risk obstruction signal."
    if room.is_heavy_storage or _contains_keyword(room_text, HEAVY_STORAGE_KEYWORDS):
        return "HIGH_RISK", "Heavy storage at the center zone is treated as a high-risk obstruction signal."
    if room.is_structural_element or room.type == "staircase" or _contains_keyword(room_text, REVIEW_KEYWORDS):
        return "NEEDS_REVIEW", "Structural, stair, shaft, or duct use at the center zone requires manual review."
    if room.center_treatment and "furniture" in room.center_treatment.lower():
        return _apply_plot_context(
            "WARNING",
            "Furniture-heavy use at the center zone should be treated as a warning, not an automatic failure.",
            plan,
        )
    if room.is_open_area or room.type in OK_ROOM_TYPES or _contains_keyword(room_text, POSITIVE_CENTER_KEYWORDS):
        return "OK", "Open living, dining, circulation, atrium, or light-well style use can be acceptable provisional center treatment."
    if room.type in {"bedroom", "master_bedroom", "kitchen"}:
        return _apply_plot_context(
            "WARNING",
            "Bedroom or kitchen overlap at the center zone should be treated as a warning-level obstruction risk.",
            plan,
        )
    return _apply_plot_context(
        "WARNING",
        "Enclosed room overlap at the center zone should be treated as a warning-level obstruction risk.",
        plan,
    )


def _iter_service_rectangles(plan: FloorPlanSchema) -> list[dict[str, Any]]:
    service_rectangles: list[dict[str, Any]] = []

    def add_service_entry(group_name: str, item: dict[str, Any], index: int) -> None:
        if not all(key in item for key in ("x", "y", "width", "height")):
            return
        try:
            rect = Rect(
                x=float(item["x"]),
                y=float(item["y"]),
                width=float(item["width"]),
                height=float(item["height"]),
            )
        except (TypeError, ValueError):
            return
        service_rectangles.append(
            {
                "id": str(item.get("id") or f"{group_name}_{index + 1}"),
                "name": str(item.get("name") or item.get("type") or group_name),
                "type": str(item.get("type") or group_name),
                "group": group_name,
                "rect": rect,
                "is_wet_service": bool(item.get("is_wet_service")),
                "is_structural_element": bool(item.get("is_structural_element")),
                "notes": item.get("notes") if isinstance(item.get("notes"), list) else [],
            }
        )

    for group_name, raw_value in plan.services.items():
        if isinstance(raw_value, dict):
            add_service_entry(group_name, raw_value, 0)
        elif isinstance(raw_value, list):
            for index, item in enumerate(raw_value):
                if isinstance(item, dict):
                    add_service_entry(group_name, item, index)

    return service_rectangles


def _classify_service_overlap(service: dict[str, Any], plan: FloorPlanSchema) -> tuple[str, str]:
    service_text = _normalized_text(service["group"], service["type"], service["name"], service["notes"])
    if service.get("is_wet_service") or _contains_keyword(service_text, WET_SERVICE_KEYWORDS):
        return "HIGH_RISK", "Wet-service or septic-style service overlap at the center zone is a high-risk obstruction signal."
    if service.get("is_structural_element") or _contains_keyword(service_text, REVIEW_KEYWORDS):
        return "NEEDS_REVIEW", "Structural, shaft, duct, or stair-like service overlap at the center zone requires manual review."
    if _contains_keyword(service_text, POSITIVE_CENTER_KEYWORDS):
        return "OK", "Light-well, atrium, or open-courtyard service treatment can be acceptable provisional center treatment."
    return _apply_plot_context(
        "WARNING",
        "Explicit service overlap at the center zone should be reviewed as a warning-level obstruction risk.",
        plan,
    )


def _classify_parking_overlap(plan: FloorPlanSchema) -> tuple[str, str]:
    return _apply_plot_context(
        "WARNING",
        "Parking overlap at the center zone should be treated as a provisional obstruction warning, not an automatic failure.",
        plan,
    )


def _vertical_stack_severity(
    *,
    lower_room: RoomSchema,
    upper_room: RoomSchema,
    stack_rect: Rect,
    brahmasthan: Rect,
) -> tuple[str, str]:
    stack_in_center = _overlap_area(stack_rect, brahmasthan) > 0
    upper_is_wet = upper_room.type in WET_ROOM_TYPES or bool(upper_room.is_wet_service)
    upper_is_heavy = (
        upper_room.type in HEAVY_STACK_ROOM_TYPES
        or bool(upper_room.is_heavy_storage)
        or bool(upper_room.is_structural_element)
    )
    staircase_in_stack = lower_room.type == "staircase" or upper_room.type == "staircase"
    lower_is_sensitive = lower_room.type in SENSITIVE_STACK_ROOM_TYPES

    if staircase_in_stack:
        return (
            "review",
            f"Vertical stacking review observation: stack involving staircase '{lower_room.name if lower_room.type == 'staircase' else upper_room.name}' needs structural and circulation review; this is not automatic failure or official Vastu scoring.",
        )
    if upper_is_wet and lower_is_sensitive:
        return (
            "review",
            f"Vertical stacking review observation: {upper_room.type} '{upper_room.name}' is above {lower_room.type} '{lower_room.name}'. Structural, MEP, drainage, and planning review is needed; this is not automatic failure or official Vastu scoring.",
        )
    if upper_is_heavy and lower_is_sensitive:
        return (
            "review",
            f"Vertical stacking review observation: {upper_room.type} '{upper_room.name}' is above sensitive room '{lower_room.name}'. Structural and planning review is needed; this is not automatic failure or official Vastu scoring.",
        )
    if stack_in_center:
        return (
            "review",
            "Vertical stacking review observation: stacked room footprint intersects the provisional Brahmasthan center zone. Review is needed, but this is not automatic failure or official Vastu scoring.",
        )
    return (
        "info",
        f"Vertical stacking review observation: {upper_room.type} '{upper_room.name}' is above {lower_room.type} '{lower_room.name}'. This is recorded for multi-floor review only and is not automatic failure or official Vastu scoring.",
    )


def _build_vertical_stack_observations(
    plan: FloorPlanSchema,
    room_rects: dict[str, Rect],
    brahmasthan: Rect,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    rooms = sorted(plan.rooms, key=lambda room: (room.level, room.id))
    for index, room in enumerate(rooms):
        for other in rooms[index + 1 :]:
            if room.level == other.level:
                continue
            stack_rect = _intersection_rect(room_rects[room.id], room_rects[other.id])
            if stack_rect is None:
                continue
            lower_room, upper_room = (room, other) if room.level < other.level else (other, room)
            lower_rect = room_rects[lower_room.id]
            upper_rect = room_rects[upper_room.id]
            overlap_area = stack_rect.area
            severity, message = _vertical_stack_severity(
                lower_room=lower_room,
                upper_room=upper_room,
                stack_rect=stack_rect,
                brahmasthan=brahmasthan,
            )
            observations.append(
                {
                    "label": "vertical stacking review observation",
                    "lower_room_id": lower_room.id,
                    "lower_room_type": lower_room.type,
                    "lower_level": lower_room.level,
                    "upper_room_id": upper_room.id,
                    "upper_room_type": upper_room.type,
                    "upper_level": upper_room.level,
                    "overlap_area_sqft": overlap_area,
                    "overlap_ratio_lower": round(overlap_area / lower_rect.area, 4) if lower_rect.area else 0.0,
                    "overlap_ratio_upper": round(overlap_area / upper_rect.area, 4) if upper_rect.area else 0.0,
                    "severity": severity,
                    "message": message,
                    "evidence_classification": "POSSIBLE_ISSUE_REQUIRES_REVIEW",
                    "official_scoring_used": False,
                }
            )
    return observations

def _build_brahmasthan_observation(
    plan: FloorPlanSchema,
    room_rects: dict[str, Rect],
) -> tuple[dict[str, Any], ValidationIssue | None]:
    brahmasthan = brahmasthan_bounds(plan.plot.width_ft, plan.plot.depth_ft)
    overlaps: list[dict[str, Any]] = []
    max_status = "OK"
    total_overlap_ratio = 0.0
    positive_treatment_detected = _positive_treatment_present(plan)

    for room in plan.rooms:
        overlap_area = _overlap_area(room_rects[room.id], brahmasthan)
        if overlap_area <= 0:
            continue
        status, reason = _classify_room_overlap(room, plan)
        overlap_ratio = round(overlap_area / brahmasthan.area, 4)
        overlaps.append(
            {
                "entity_type": "room",
                "entity_id": room.id,
                "name": room.name,
                "category": room.type,
                "status": status,
                "reason": reason,
                "overlap_area_sqft": overlap_area,
                "overlap_ratio": overlap_ratio,
                "center_treatment": room.center_treatment,
                "is_open_area": room.is_open_area,
            }
        )
        total_overlap_ratio += overlap_ratio
        positive_treatment_detected = positive_treatment_detected or status == "OK"
        if SEVERITY_ORDER[status] > SEVERITY_ORDER[max_status]:
            max_status = status

    for parking in plan.parking:
        rect = Rect(x=parking.x, y=parking.y, width=parking.width, height=parking.height)
        overlap_area = _overlap_area(rect, brahmasthan)
        if overlap_area <= 0:
            continue
        status, reason = _classify_parking_overlap(plan)
        overlap_ratio = round(overlap_area / brahmasthan.area, 4)
        overlaps.append(
            {
                "entity_type": "parking",
                "entity_id": parking.id,
                "name": parking.id,
                "category": parking.vehicle_type,
                "status": status,
                "reason": reason,
                "overlap_area_sqft": overlap_area,
                "overlap_ratio": overlap_ratio,
            }
        )
        total_overlap_ratio += overlap_ratio
        if SEVERITY_ORDER[status] > SEVERITY_ORDER[max_status]:
            max_status = status

    for service in _iter_service_rectangles(plan):
        overlap_area = _overlap_area(service["rect"], brahmasthan)
        if overlap_area <= 0:
            continue
        status, reason = _classify_service_overlap(service, plan)
        overlap_ratio = round(overlap_area / brahmasthan.area, 4)
        overlaps.append(
            {
                "entity_type": "service",
                "entity_id": service["id"],
                "name": service["name"],
                "category": service["type"],
                "status": status,
                "reason": reason,
                "overlap_area_sqft": overlap_area,
                "overlap_ratio": overlap_ratio,
            }
        )
        total_overlap_ratio += overlap_ratio
        positive_treatment_detected = positive_treatment_detected or status == "OK"
        if SEVERITY_ORDER[status] > SEVERITY_ORDER[max_status]:
            max_status = status

    total_overlap_ratio = round(min(total_overlap_ratio, 1.0), 4)
    notes = [
        "This is provisional. No approved Vastu rules are used for official scoring.",
        "Open-to-sky treatment is ideal only when feasible and is not required for small houses, apartments, or multi-floor buildings.",
    ]
    if plan.plot.small_plot_context:
        notes.append("Small-plot context is marked on the plan and should be considered when interpreting center-zone overlap.")
    if plan.plot.multi_floor_context:
        notes.append("Multi-floor context is marked on the plan and should be considered when interpreting center-zone overlap.")
    if plan.brahmasthan_treatment is not None:
        notes.append("Plan-level Brahmasthan treatment metadata is present and is used only as provisional context.")

    if overlaps and total_overlap_ratio >= 0.95 and not positive_treatment_detected:
        max_status = "HIGH_RISK"
        notes.append("The center zone appears substantially blocked without a clear positive light or air treatment marker.")

    if overlaps:
        summary = f"{len(overlaps)} room, parking, or explicit service overlap(s) were found in the center 1/9 zone."
    else:
        summary = "No room, parking, or explicit service rectangles overlap the center 1/9 zone."

    observation = {
        "label": "Brahmasthan center-zone observation",
        "status": max_status,
        "provisional": True,
        "center_zone_definition": "center_1_over_9_of_rectangular_plot",
        "bounds": {
            "x": brahmasthan.x,
            "y": brahmasthan.y,
            "width": brahmasthan.width,
            "height": brahmasthan.height,
        },
        "summary": summary,
        "total_overlap_ratio": total_overlap_ratio,
        "open_to_sky_required": False,
        "official_vastu_scoring_used": False,
        "evidence_classification": "PROVISIONAL_VASTU_NOTE",
        "treatment": plan.brahmasthan_treatment.model_dump(mode="json") if plan.brahmasthan_treatment else None,
        "overlaps": overlaps,
        "notes": notes,
    }

    if max_status == "OK":
        return observation, None

    issue_message_map = {
        "WARNING": "Brahmasthan obstruction risk observed in the provisional center-zone analysis.",
        "NEEDS_REVIEW": "Brahmasthan center-zone observation needs manual review because of ambiguity or obstructive use.",
        "HIGH_RISK": "Brahmasthan obstruction risk is high in the provisional center-zone analysis.",
    }
    issue = ValidationIssue(
        severity="warning",
        code="brahmasthan_obstruction_risk",
        message=issue_message_map[max_status],
        location="plot.brahmasthan",
    )
    return observation, issue


def _rotated_plot_warning(plan: FloorPlanSchema) -> ValidationIssue | None:
    if abs(plan.plot.north_angle_deg) < 0.01:
        return None
    return ValidationIssue(
        severity="warning",
        code="rotated_plot_axis_alignment_warning",
        message=(
            "Zone tagging is provisional for rotated plots because Phase 1 uses an axis-aligned 3x3 centroid grid and does not rotate room zones by north_angle_deg."
        ),
        location="plot.north_angle_deg",
    )


def validate_geometry(plan: FloorPlanSchema) -> tuple[list[ValidationIssue], dict[str, object]]:
    issues: list[ValidationIssue] = []
    plot_rect = _plot_rect(plan)
    room_rects = _room_rectangles(plan)
    room_zones: dict[str, str] = {}

    for room in plan.rooms:
        rect = room_rects[room.id]
        expected_area = round(room.width * room.height, 2)
        if round(room.area or 0.0, 2) != expected_area:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="room_area_mismatch",
                    message=f"Room '{room.name}' area does not match width * height.",
                    location=f"rooms.{room.id}.area",
                )
            )
        if not rect.is_inside(plot_rect):
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="room_outside_plot",
                    message=f"Room '{room.name}' extends outside the plot boundary.",
                    location=f"rooms.{room.id}",
                )
            )
        room_zones[room.id] = classify_room_zone(plan.plot.width_ft, plan.plot.depth_ft, rect)

    # A planner draft may carry a canonical geometry model. Use the local
    # runtime solver for its additional clearance and size observations.
    # Plan-level checks (area mismatch, outside plot) still run above.
    from knowledge_engine.planning.geometry_model import validate_model as _solver_validate

    _skip_overlap_check = False
    canonical_model = getattr(plan, "geometry_model", None)
    if canonical_model and "rooms" in canonical_model:
        solver_result = _solver_validate(canonical_model)
        for overlap in solver_result.get("overlaps", []):
            issues.append(ValidationIssue(
                severity="error",
                code="room_overlap",
                message=f"Rooms '{overlap['room_a']}' and '{overlap['room_b']}' overlap.",
                location=f"rooms.{overlap['room_a']},rooms.{overlap['room_b']}",
            ))
        for cv in solver_result.get("clearance_violations", []):
            issues.append(ValidationIssue(
                severity="error",
                code="clearance_violation",
                message=f"Rooms '{cv['room_a']}' and '{cv['room_b']}' have insufficient clearance.",
                location=f"rooms.{cv['room_a']},rooms.{cv['room_b']}",
            ))
        for sv in solver_result.get("minimum_size_violations", []):
            issues.append(ValidationIssue(
                severity="warning",
                code="minimum_size_violation",
                message=f"Room '{sv['room_id']}' is below minimum size.",
                location=f"rooms.{sv['room_id']}",
            ))
        if not solver_result.get("brahmasthan_clear", True):
            issues.append(ValidationIssue(
                severity="warning",
                code="brahmasthan_violation",
                message="One or more rooms overlap the Brahmasthan zone.",
                location="brahmasthan",
            ))
        _skip_overlap_check = True

    rooms_by_level: dict[int, list[RoomSchema]] = {}
    for room in plan.rooms:
        rooms_by_level.setdefault(room.level, []).append(room)

    if not _skip_overlap_check:
        for level, level_rooms in rooms_by_level.items():
            for index, room in enumerate(level_rooms):
                for other in level_rooms[index + 1 :]:
                    if rectangles_overlap(room_rects[room.id], room_rects[other.id]):
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                code="room_overlap",
                                message=f"Rooms '{room.id}' and '{other.id}' overlap on level {level}.",
                                location=f"rooms.{room.id},rooms.{other.id}",
                            )
                        )

    parking_rects: list[dict[str, object]] = []
    for parking in plan.parking:
        rect = Rect(x=parking.x, y=parking.y, width=parking.width, height=parking.height)
        parking_inside = rect.is_inside(plot_rect)
        if not parking_inside:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="parking_outside_plot",
                    message=f"Parking '{parking.id}' extends outside the plot boundary.",
                    location=f"parking.{parking.id}",
                )
            )
        for room in plan.rooms:
            if room.level != parking.level:
                continue
            room_rect = room_rects[room.id]
            if rectangles_overlap(rect, room_rect):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="parking_room_overlap",
                        message=f"Parking '{parking.id}' overlaps room '{room.id}' on level {parking.level}.",
                        location=f"parking.{parking.id},rooms.{room.id}",
                    )
                )
        parking_rects.append({"id": parking.id, "inside_plot": parking_inside, "level": parking.level})

    rotated_plot_issue = _rotated_plot_warning(plan)
    if rotated_plot_issue is not None:
        issues.append(rotated_plot_issue)

    multi_floor_issue: ValidationIssue | None = None
    if plan.metadata.level_count > 1:
        multi_floor_issue = ValidationIssue(
            severity="info",
            code="multi_floor_level_aware_overlap_check",
            message="Multi-floor layout detected. 2D overlap validation is level-aware; vertical stacking checks are separate.",
            location="metadata.level_count",
        )
        issues.append(multi_floor_issue)

    brahmasthan = brahmasthan_bounds(plan.plot.width_ft, plan.plot.depth_ft)
    vertical_stack_observations = _build_vertical_stack_observations(plan, room_rects, brahmasthan)
    brahmasthan_observation, brahmasthan_issue = _build_brahmasthan_observation(plan, room_rects)
    if brahmasthan_issue is not None:
        issues.append(brahmasthan_issue)

    geometry_data = {
        "plot_rect": plot_rect,
        "room_zones": room_zones,
        "zone_tagging_mode": "axis_aligned_centroid_3x3_grid",
        "rotation_warning_applies": rotated_plot_issue is not None,
        "level_aware_overlap_validation": True,
        "multi_floor_warning_applies": multi_floor_issue is not None,
        "brahmasthan_bounds": {
            "x": brahmasthan.x,
            "y": brahmasthan.y,
            "width": brahmasthan.width,
            "height": brahmasthan.height,
        },
        "brahmasthan_observation": brahmasthan_observation,
        "vertical_stack_observations": vertical_stack_observations,
        "parking_checks": parking_rects,
    }
    return issues, geometry_data





