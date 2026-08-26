"""Fail-closed architect-usability assessment for generated plan drafts."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from knowledge_engine.domain.findings import ValidationReport
from knowledge_engine.domain.floor_plan import FloorPlanSchema
from knowledge_engine.validation.practical import AREA_RULES, DOOR_REQUIRED_ROOM_TYPES, VENTILATION_REQUIRED_ROOM_TYPES


@dataclass(frozen=True)
class ArchitectUsabilityAssessment:
    """A planning gate, not a legal, structural, or compliance decision."""

    architect_usable: bool
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _has_ventilation(room: object) -> bool:
    windows = getattr(room, "windows", [])
    vents = getattr(room, "vents", [])
    notes = " ".join(str(note).lower() for note in getattr(room, "notes", []))
    return bool(windows or vents or any(marker in notes for marker in ("vent", "window", "exhaust")))


def _parking_touches_road(plan: FloorPlanSchema) -> bool:
    if not plan.parking:
        return False
    tolerance = 0.05
    side = plan.plot.road_side.strip().lower()
    for parking in plan.parking:
        if parking.level != 0 or parking.inside_plot is False:
            continue
        if side == "north" and abs((parking.y + parking.height) - plan.plot.depth_ft) <= tolerance:
            return True
        if side == "south" and abs(parking.y) <= tolerance:
            return True
        if side == "east" and abs((parking.x + parking.width) - plan.plot.width_ft) <= tolerance:
            return True
        if side == "west" and abs(parking.x) <= tolerance:
            return True
    return False


def assess_architect_usability(plan: FloorPlanSchema, report: ValidationReport) -> ArchitectUsabilityAssessment:
    """Reject drafts that cannot responsibly be called architect-review ready.

    The deterministic validation report remains available. This additional gate
    prevents a geometry-only pass from being presented as a usable plan when
    basic program, circulation, access, opening, or ventilation evidence is
    absent. It does not claim legal/code compliance.
    """

    blockers: list[str] = []
    warnings: list[str] = []

    if not report.geometry_valid:
        blockers.append("geometry validation did not pass")
    if not report.requirements_valid:
        blockers.append("required-space validation did not pass")
    if any(issue.code in {"clearance_violation", "CLEARANCE_VIOLATION"} for issue in report.issues):
        blockers.append("one or more clearance violations were reported")

    for room in plan.rooms:
        area_rule = AREA_RULES.get(room.type)
        if area_rule is not None and room.area is not None and room.area < area_rule[0]:
            blockers.append(
                f"{room.type} '{room.name}' is below the {area_rule[0]:.0f} sqft planning heuristic"
            )
        if room.type in DOOR_REQUIRED_ROOM_TYPES and not room.is_open_area and not room.doors:
            blockers.append(f"door information is missing for '{room.name}'")
        if room.type in VENTILATION_REQUIRED_ROOM_TYPES and not _has_ventilation(room):
            blockers.append(f"ventilation information is missing for '{room.name}'")

    if (plan.requirements.bhk or 0) >= 2 and not any(room.type == "circulation" for room in plan.rooms):
        blockers.append("circulation space is missing for a 2BHK-or-larger program")

    if plan.requirements.requires_parking:
        if not plan.parking:
            blockers.append("required parking is missing")
        elif not _parking_touches_road(plan):
            blockers.append("parking does not have deterministic road-side access")

    if plan.metadata.level_count > 1 and not any(room.type == "staircase" for room in plan.rooms):
        blockers.append("multi-level configuration is missing a staircase")

    for issue in report.issues:
        if issue.evidence_classification == "PROVISIONAL_VASTU_NOTE":
            warnings.append(issue.message)

    deduplicated_blockers = tuple(dict.fromkeys(blockers))
    return ArchitectUsabilityAssessment(
        architect_usable=not deduplicated_blockers,
        blockers=deduplicated_blockers,
        warnings=tuple(dict.fromkeys(warnings)),
    )
