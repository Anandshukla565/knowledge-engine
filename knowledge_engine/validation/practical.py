from __future__ import annotations

from typing import Any

from knowledge_engine.domain.findings import ValidationIssue
from knowledge_engine.domain.floor_plan import FloorPlanSchema, RoomSchema

PRACTICAL_CHECKS_MESSAGE = "Practical checks are MVP heuristics, not legal/code compliance."
PRACTICAL_ISSUE_CODES = {
    "small_bedroom_area",
    "small_master_bedroom_area",
    "small_kitchen_area",
    "small_toilet_area",
    "small_bathroom_area",
    "small_pooja_area",
    "small_parking_size",
    "missing_circulation",
    "missing_room_door",
    "missing_service_ventilation",
}
PRACTICAL_ISSUE_WEIGHTS = {
    "small_bedroom_area": 4,
    "small_master_bedroom_area": 4,
    "small_kitchen_area": 4,
    "small_toilet_area": 4,
    "small_bathroom_area": 4,
    "small_pooja_area": 2,
    "small_parking_size": 5,
    "missing_circulation": 8,
    "missing_room_door": 2,
    "missing_service_ventilation": 5,
}
DOOR_REQUIRED_ROOM_TYPES = {
    "living",
    "kitchen",
    "bedroom",
    "master_bedroom",
    "toilet",
    "bathroom",
    "pooja",
    "dining",
    "utility",
    "staircase",
    "other",
}
VENTILATION_REQUIRED_ROOM_TYPES = {"kitchen", "toilet", "bathroom"}
AREA_RULES = {
    "bedroom": (80.0, "small_bedroom_area", "Bedroom"),
    "master_bedroom": (100.0, "small_master_bedroom_area", "Master bedroom"),
    "kitchen": (50.0, "small_kitchen_area", "Kitchen"),
    "toilet": (18.0, "small_toilet_area", "Toilet"),
    "bathroom": (25.0, "small_bathroom_area", "Bathroom"),
}


def _note_text(room: RoomSchema) -> str:
    return " ".join(str(note).strip().lower() for note in room.notes if note)


def _has_ventilation_marker(room: RoomSchema) -> bool:
    if room.windows or room.vents:
        return True
    notes_text = _note_text(room)
    return any(keyword in notes_text for keyword in ("vent", "ventilation", "window", "exhaust"))


def _parking_dims_satisfy_minimum(width: float, height: float) -> bool:
    smaller = min(width, height)
    larger = max(width, height)
    return smaller >= 8.0 and larger >= 14.0


def _make_issue(*, severity: str, code: str, message: str, location: str) -> ValidationIssue:
    return ValidationIssue(severity=severity, code=code, message=message, location=location)


def validate_practical(plan: FloorPlanSchema) -> tuple[list[ValidationIssue], dict[str, Any]]:
    issues: list[ValidationIssue] = []

    for room in plan.rooms:
        area_rule = AREA_RULES.get(room.type)
        if area_rule is not None:
            minimum_area, issue_code, label = area_rule
            if room.area < minimum_area:
                issues.append(
                    _make_issue(
                        severity="warning",
                        code=issue_code,
                        message=(
                            f"{label} '{room.name}' area is {room.area} sqft. MVP practical heuristic expects at least {minimum_area} sqft; this is not legal/code compliance."
                        ),
                        location=f"rooms.{room.id}.area",
                    )
                )

        if room.type == "pooja" and room.area < 8.0:
            severity = "info" if room.area >= 6.0 else "warning"
            issues.append(
                _make_issue(
                    severity=severity,
                    code="small_pooja_area",
                    message=(
                        f"Pooja room '{room.name}' area is {room.area} sqft. MVP practical heuristic expects at least 8.0 sqft; this is not legal/code compliance."
                    ),
                    location=f"rooms.{room.id}.area",
                )
            )

        if room.type in DOOR_REQUIRED_ROOM_TYPES and not room.is_open_area and len(room.doors) == 0:
            issues.append(
                _make_issue(
                    severity="warning",
                    code="missing_room_door",
                    message=(
                        f"Door information was not supplied in the input for room '{room.name}'. Confirm the design documentation before treating this as a design issue."
                    ),
                    location=f"rooms.{room.id}.doors",
                )
            )

        if room.type in VENTILATION_REQUIRED_ROOM_TYPES and not _has_ventilation_marker(room):
            issues.append(
                _make_issue(
                    severity="warning",
                    code="missing_service_ventilation",
                    message=(
                        f"Window or ventilation information was not supplied in the input for {room.type.replace('_', ' ')} '{room.name}'. Confirm the design documentation."
                    ),
                    location=f"rooms.{room.id}.windows",
                )
            )

    if (plan.requirements.bhk or 0) >= 2 and not any(room.type == "circulation" for room in plan.rooms):
        issues.append(
            _make_issue(
                severity="warning",
                code="missing_circulation",
                message=(
                    "Circulation-space information was not supplied in the input for this 2BHK-or-larger program. Confirm whether a hall, lobby, stair connector, or other circulation space exists in the design documentation."
                ),
                location="rooms",
            )
        )

    for parking in plan.parking:
        if not _parking_dims_satisfy_minimum(parking.width, parking.height):
            issues.append(
                _make_issue(
                    severity="warning",
                    code="small_parking_size",
                    message=(
                        f"Parking '{parking.id}' is {parking.width} ft x {parking.height} ft. MVP practical heuristic expects at least 8 ft x 14 ft in either orientation; this is not legal/code compliance."
                    ),
                    location=f"parking.{parking.id}",
                )
            )

    return issues, {
        "practical_checks_message": PRACTICAL_CHECKS_MESSAGE,
        "practical_issue_count": len(issues),
        "practical_issue_codes": [issue.code for issue in issues],
    }
