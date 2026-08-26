from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from knowledge_engine.intake.confirmation_models import (
    CONFIRMATION_STATUS,
    CONFIDENCE,
    EVIDENCE_TYPE,
    FACT_CATEGORY,
    READINESS_RESULT,
    BlockingItem,
    CompletenessSummary,
    Fact,
    InferredItem,
    InputConfirmation,
    MissingItem,
    Provenance,
    ResponseBatch,
    ResponseItem,
    ValidationReadiness,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PRODUCT_CHECKPOINT = "PRODUCT_MVP_PHASE_013_LOCAL_PDF_INSPECTOR_AND_CORRECTION_CHECKLIST"

REQUIRED_GEOMETRY_FACTS = {
    "PLOT_DIMENSIONS",
    "ORIENTATION_NORTH",
    "LEVEL_COUNT",
    "ROOM_IDENTITIES",
    "ROOM_GEOMETRY",
    "ROOM_TO_LEVEL_ASSIGNMENT",
}

REQUIRED_PRACTICAL_FACTS = {
    "OPENINGS_SUMMARY",
    "CIRCULATION_ACCESS",
}

REQUIRED_SPECIFIC_FACTS = {
    "SOURCE_PROVENANCE",
}

OPTIONAL_FACTS = {
    "VENTILATION_INFO",
    "STAIRCASE_INFO",
    "PARKING_STATUS",
    "POOJA_STATUS",
    "ROOM_NOTES",
    "BRAHMASTHAN_TREATMENT",
    "SERVICES_INFO",
}

REQUIRED_SPECIFIC_BLOCKING = {
    "STAIRCASE_INFO",
    "PARKING_STATUS",
    "POOJA_STATUS",
}

REVIEWED_DRAWING_PROVENANCE_REQUIREMENTS = (
    ("REVIEWED_DRAWING_REVIEW_STATUS", "Reviewed drawing review status"),
    ("REVIEWED_DRAWING_DOCUMENT_ID", "Reviewed drawing source document ID"),
    ("REVIEWED_DRAWING_DOCUMENT_CHECKSUM", "Reviewed drawing source document SHA-256 checksum"),
    ("REVIEWED_DRAWING_SOURCE_PAGES", "Reviewed drawing source page numbers"),
    ("REVIEWED_DRAWING_REVIEWER", "Reviewed drawing reviewer accountability metadata"),
    ("REVIEWED_DRAWING_REVIEWED_AT", "Reviewed drawing review timestamp"),
    ("REVIEWED_DRAWING_UNRESOLVED_ITEMS", "Reviewed drawing unresolved-items confirmation"),
)

# ---------------------------------------------------------------------------
# Input parsing helpers
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read()
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _try_parse_floor_plan(data: dict[str, Any]) -> dict[str, Any] | None:
    """Return data dict if it matches the current FloorPlanSchema shape."""
    if not isinstance(data, dict):
        return None
    required_top = {"metadata", "plot", "requirements", "rooms"}
    if not required_top.issubset(data.keys()):
        return None
    return data


def _try_parse_validation_report(data: dict[str, Any]) -> dict[str, Any] | None:
    """Return data dict if it matches a phase1 validation report shape."""
    if not isinstance(data, dict):
        return None
    if "validation_status" in data and "plot_summary" in data:
        return data
    return None


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdefABCDEF" for character in value
    )


def _reviewed_drawing_provenance_missing(input_provenance: Any) -> list[tuple[str, str]]:
    """Return missing-input labels for pilot reviewed-drawing provenance.

    This is a pilot confirmation boundary only. It does not inspect PDFs or
    judge whether the supplied JSON matches a source drawing.
    """

    if not isinstance(input_provenance, dict):
        return list(REVIEWED_DRAWING_PROVENANCE_REQUIREMENTS)

    failures: list[tuple[str, str]] = []
    if input_provenance.get("review_status") != "reviewed":
        failures.append(REVIEWED_DRAWING_PROVENANCE_REQUIREMENTS[0])
    if not isinstance(input_provenance.get("source_document_id"), str) or not input_provenance["source_document_id"].strip():
        failures.append(REVIEWED_DRAWING_PROVENANCE_REQUIREMENTS[1])
    if not _is_sha256(input_provenance.get("source_document_checksum")):
        failures.append(REVIEWED_DRAWING_PROVENANCE_REQUIREMENTS[2])
    page_numbers = input_provenance.get("source_page_numbers")
    if not isinstance(page_numbers, list) or not page_numbers or any(
        not isinstance(page, int) or isinstance(page, bool) or page < 1 for page in page_numbers
    ):
        failures.append(REVIEWED_DRAWING_PROVENANCE_REQUIREMENTS[3])
    if not isinstance(input_provenance.get("reviewed_by"), str) or not input_provenance["reviewed_by"].strip():
        failures.append(REVIEWED_DRAWING_PROVENANCE_REQUIREMENTS[4])
    if not isinstance(input_provenance.get("reviewed_at"), str) or not input_provenance["reviewed_at"].strip():
        failures.append(REVIEWED_DRAWING_PROVENANCE_REQUIREMENTS[5])
    if input_provenance.get("unresolved_items") != []:
        failures.append(REVIEWED_DRAWING_PROVENANCE_REQUIREMENTS[6])
    return failures


def _load_input(input_path: Path) -> tuple[dict[str, Any], str]:
    """Load and classify input file. Returns (data_dict, input_kind)."""
    raw = input_path.read_text(encoding="utf-8", errors="replace")
    data = json.loads(raw)

    kind = "unknown"
    if _try_parse_floor_plan(data) is not None:
        kind = "floor_plan"
    elif _try_parse_validation_report(data) is not None:
        kind = "validation_report"

    return data, kind


# ---------------------------------------------------------------------------
# Fact extraction
# ---------------------------------------------------------------------------


def _provenance(source_path: str, source_field: str, note: str | None = None) -> Provenance:
    return Provenance(source_path=source_path, source_field=source_field, derivation_note=note)


def _fact(fact_id: str, label: str, value: Any, evidence: EvidenceType, category: FactCategory,
          source_path: str, source_field: str, note: str | None = None) -> Fact:
    return Fact(
        fact_id=fact_id,
        label=label,
        value=value,
        evidence_type=evidence,
        category=category,
        provenance=_provenance(source_path, source_field, note),
    )


def _missing(fact_id: str, label: str, category: FactCategory, blocking: bool = False,
             required_for: list[str] | None = None) -> MissingItem:
    return MissingItem(
        fact_id=fact_id,
        label=label,
        category=category,
        blocking=blocking,
        required_for=required_for or [],
    )


def _inferred(fact_id: str, label: str, inferred_value: Any, confidence: CONFIDENCE,
              category: FactCategory, blocking: bool = False, note: str | None = None) -> InferredItem:
    return InferredItem(
        fact_id=fact_id,
        label=label,
        inferred_value=inferred_value,
        confidence=confidence,
        category=category,
        blocking=blocking,
        derivation_note=note,
    )


def _blocking(item_id: str, label: str, reason: str, related: list[str] | None = None) -> BlockingItem:
    return BlockingItem(item_id=item_id, label=label, reason=reason, related_fact_ids=related or [])


def _non_blocking(item_id: str, label: str, reason: str, related: list[str] | None = None) -> BlockingItem:
    return BlockingItem(item_id=item_id, label=label, reason=reason, related_fact_ids=related or [])


def extract_facts_from_floor_plan(data: dict[str, Any]) -> tuple[list[Fact], list[MissingItem], list[InferredItem]]:
    """Extract facts from a parsed FloorPlanSchema-shaped dict."""
    facts: list[Fact] = []
    missing: list[MissingItem] = []
    inferred: list[InferredItem] = []

    metadata = data.get("metadata", {})
    plot = data.get("plot", {})
    requirements = data.get("requirements", {})
    rooms = data.get("rooms", [])
    parking_list = data.get("parking", [])
    services = data.get("services", {})
    brahmasthan = data.get("brahmasthan_treatment")
    input_provenance = data.get("input_provenance")

    # ---- Metadata facts ----
    facts.append(_fact("PROJECT_NAME", "Project name", metadata.get("project_name"),
                        "EXPLICIT_INPUT", "required_geometry", "metadata", "project_name"))
    facts.append(_fact("PLAN_ID", "Plan identifier", metadata.get("plan_id"),
                        "EXPLICIT_INPUT", "required_geometry", "metadata", "plan_id"))
    facts.append(_fact("UNITS", "Units", metadata.get("units", "ft"),
                        "EXPLICIT_INPUT", "required_geometry", "metadata", "units"))
    facts.append(_fact("LEVEL_COUNT", "Number of levels", metadata.get("level_count", 1),
                        "EXPLICIT_INPUT", "required_geometry", "metadata", "level_count"))

    # ---- Plot facts ----
    width = plot.get("width_ft")
    depth = plot.get("depth_ft")
    facing = plot.get("facing")
    road_side = plot.get("road_side")
    north_angle = plot.get("north_angle_deg", 0.0)

    if width is not None and depth is not None:
        facts.append(_fact("PLOT_DIMENSIONS", "Plot dimensions",
                            {"width_ft": width, "depth_ft": depth},
                            "EXPLICIT_INPUT", "required_geometry", "plot", "width_ft, depth_ft"))
        plot_area = round(float(width) * float(depth), 2)
        inferred.append(_inferred("PLOT_AREA", "Plot area (sq ft)", plot_area, "high",
                                   "required_geometry", note="Derived from width_ft * depth_ft."))
    else:
        if width is None:
            missing.append(_missing("PLOT_WIDTH", "Plot width (ft)", "required_geometry", blocking=True,
                                     required_for=["geometry_validation"]))
        if depth is None:
            missing.append(_missing("PLOT_DEPTH", "Plot depth (ft)", "required_geometry", blocking=True,
                                     required_for=["geometry_validation"]))

    if facing:
        facts.append(_fact("ORIENTATION_FACING", "Plot facing", facing,
                            "EXPLICIT_INPUT", "required_geometry", "plot", "facing"))
    else:
        missing.append(_missing("ORIENTATION_FACING", "Plot facing direction", "required_geometry",
                                 blocking=True, required_for=["geometry_validation", "practical_review"]))

    if road_side:
        facts.append(_fact("ROAD_SIDE", "Road side", road_side,
                            "EXPLICIT_INPUT", "required_geometry", "plot", "road_side"))
    else:
        missing.append(_missing("ROAD_SIDE", "Road side / entrance side", "required_geometry",
                                 blocking=True, required_for=["geometry_validation", "practical_review"]))

    facts.append(_fact("NORTH_ANGLE", "North angle (degrees)", north_angle,
                        "EXPLICIT_INPUT", "required_geometry", "plot", "north_angle_deg"))

    # ---- Requirements facts ----
    bhk = requirements.get("bhk")
    if bhk is not None:
        facts.append(_fact("BHK", "BHK configuration", bhk,
                            "EXPLICIT_INPUT", "required_practical", "requirements", "bhk"))
    else:
        missing.append(_missing("BHK", "BHK configuration", "required_practical", blocking=False,
                                 required_for=["practical_review"]))

    requires_parking = requirements.get("requires_parking", False)
    requires_pooja = requirements.get("requires_pooja", False)
    single_story = requirements.get("single_story_only", True)
    required_room_types = requirements.get("required_room_types", [])

    facts.append(_fact("REQUIRES_PARKING", "Parking required", requires_parking,
                        "EXPLICIT_INPUT", "required_specific", "requirements", "requires_parking"))
    facts.append(_fact("REQUIRES_POOJA", "Pooja required", requires_pooja,
                        "EXPLICIT_INPUT", "required_specific", "requirements", "requires_pooja"))
    facts.append(_fact("SINGLE_STORY_ONLY", "Single-story only", single_story,
                        "EXPLICIT_INPUT", "required_specific", "requirements", "single_story_only"))

    if required_room_types:
        facts.append(_fact("REQUIRED_ROOM_TYPES", "Required room types", required_room_types,
                            "EXPLICIT_INPUT", "required_practical", "requirements", "required_room_types"))

    # ---- Room facts ----
    if not rooms:
        missing.append(_missing("ROOM_IDENTITIES", "Room identities", "required_geometry",
                                 blocking=True, required_for=["geometry_validation", "practical_review"]))
        missing.append(_missing("ROOM_GEOMETRY", "Room geometry/dimensions", "required_geometry",
                                 blocking=True, required_for=["geometry_validation"]))
        missing.append(_missing("ROOM_TO_LEVEL_ASSIGNMENT", "Room-to-level assignment", "required_geometry",
                                 blocking=True, required_for=["geometry_validation"]))
    else:
        room_ids = [r.get("id", f"room_{i}") for i, r in enumerate(rooms)]
        facts.append(_fact("ROOM_IDENTITIES", "Room identifiers", room_ids,
                            "EXPLICIT_INPUT", "required_geometry", "rooms", "id"))

        room_types = [r.get("type") for r in rooms]
        facts.append(_fact("ROOM_TYPES", "Room types", room_types,
                            "EXPLICIT_INPUT", "required_geometry", "rooms", "type"))

        room_names = [r.get("name") for r in rooms]
        facts.append(_fact("ROOM_NAMES", "Room names", room_names,
                            "EXPLICIT_INPUT", "required_practical", "rooms", "name"))

        for i, room in enumerate(rooms):
            rid = room.get("id", f"room_{i}")
            rtype = room.get("type")
            rlevel = room.get("level", 0)
            rwidth = room.get("width")
            rheight = room.get("height")
            rarea = room.get("area")
            rdoors = room.get("doors", [])
            rwindows = room.get("windows", [])
            rvents = room.get("vents", [])

            prefix = f"rooms[{i}]"

            if rwidth is not None and rheight is not None:
                facts.append(_fact(f"ROOM_{rid.upper()}_DIMENSIONS",
                                    f"Dimensions for {rid}",
                                    {"width": rwidth, "height": rheight},
                                    "EXPLICIT_INPUT", "required_geometry", prefix, "width, height"))
                if rarea is None:
                    calc_area = round(float(rwidth) * float(rheight), 2)
                    inferred.append(_inferred(f"ROOM_{rid.upper()}_AREA",
                                               f"Area for {rid} (sq ft)", calc_area, "high",
                                               "required_geometry", note=f"Derived from width * height for {rid}."))
                else:
                    facts.append(_fact(f"ROOM_{rid.upper()}_AREA", f"Area for {rid} (sq ft)", rarea,
                                        "EXPLICIT_INPUT", "required_geometry", prefix, "area"))
            else:
                missing.append(_missing(f"ROOM_{rid.upper()}_DIMENSIONS",
                                         f"Dimensions for {rid}", "required_geometry", blocking=True,
                                         required_for=["geometry_validation"]))

            facts.append(_fact(f"ROOM_{rid.upper()}_LEVEL", f"Level for {rid}", rlevel,
                                "EXPLICIT_INPUT", "required_geometry", prefix, "level"))

            if rdoors:
                facts.append(_fact(f"ROOM_{rid.upper()}_DOORS", f"Doors for {rid}", rdoors,
                                    "EXPLICIT_INPUT", "required_practical", prefix, "doors"))
            if rwindows:
                facts.append(_fact(f"ROOM_{rid.upper()}_WINDOWS", f"Windows for {rid}", rwindows,
                                    "EXPLICIT_INPUT", "required_practical", prefix, "windows"))
            if rvents:
                facts.append(_fact(f"ROOM_{rid.upper()}_VENTS", f"Ventilation for {rid}", rvents,
                                    "EXPLICIT_INPUT", "optional", prefix, "vents"))

        # Openings summary
        total_doors = sum(len(r.get("doors", [])) for r in rooms)
        total_windows = sum(len(r.get("windows", [])) for r in rooms)
        facts.append(_fact("OPENINGS_SUMMARY", "Openings summary",
                            {"total_doors": total_doors, "total_windows": total_windows},
                            "DERIVED_DETERMINISTICALLY", "required_practical", "rooms",
                            "doors + windows",
                            note="Summed across all rooms."))

        # Circulation
        circulation_rooms = [r for r in rooms if r.get("type") == "circulation"]
        if circulation_rooms:
            facts.append(_fact("CIRCULATION_ACCESS", "Circulation areas",
                                [r.get("id") for r in circulation_rooms],
                                "EXPLICIT_INPUT", "required_practical", "rooms",
                                "type=circulation"))
        else:
            # Infer whether circulation is implicit
            inferred.append(_inferred("CIRCULATION_ACCESS", "Circulation access",
                                       "Not explicitly defined", "medium", "required_practical",
                                       note="No rooms of type 'circulation' found."))

    # ---- Parking facts ----
    if requires_parking:
        if parking_list:
            parking_dims = [{"id": p.get("id"), "width": p.get("width"), "height": p.get("height"),
                             "inside_plot": p.get("inside_plot")} for p in parking_list]
            facts.append(_fact("PARKING_STATUS", "Parking dimensions", parking_dims,
                                "EXPLICIT_INPUT", "required_specific", "parking", "id, width, height"))
        else:
            missing.append(_missing("PARKING_STATUS", "Parking dimensions/location", "required_specific",
                                     blocking=True, required_for=["practical_review"]))
    else:
        if parking_list:
            facts.append(_fact("PARKING_STATUS", "Parking (not required but present)",
                                [{"id": p.get("id")} for p in parking_list],
                                "EXPLICIT_INPUT", "optional", "parking", "id"))
        else:
            facts.append(_fact("PARKING_STATUS", "Parking (not required, not provided)",
                                None, "EXPLICIT_INPUT", "optional", "requirements", "requires_parking=false"))

    # ---- Pooja facts ----
    if requires_pooja:
        pooja_rooms = [r for r in rooms if r.get("type") == "pooja"]
        if pooja_rooms:
            facts.append(_fact("POOJA_STATUS", "Pooja room", [r.get("id") for r in pooja_rooms],
                                "EXPLICIT_INPUT", "required_specific", "rooms", "type=pooja"))
        else:
            missing.append(_missing("POOJA_STATUS", "Pooja room identity and dimensions",
                                     "required_specific", blocking=True, required_for=["practical_review"]))
    else:
        pooja_rooms = [r for r in rooms if r.get("type") == "pooja"]
        if pooja_rooms:
            facts.append(_fact("POOJA_STATUS", "Pooja room (not required but present)",
                                [r.get("id") for r in pooja_rooms],
                                "EXPLICIT_INPUT", "optional", "rooms", "type=pooja"))
        else:
            facts.append(_fact("POOJA_STATUS", "Pooja (not required, not provided)",
                                None, "EXPLICIT_INPUT", "optional", "requirements", "requires_pooja=false"))

    # ---- Staircase facts ----
    staircase_rooms = [r for r in rooms if r.get("type") == "staircase"]
    if not single_story or metadata.get("level_count", 1) > 1:
        if staircase_rooms:
            facts.append(_fact("STAIRCASE_INFO", "Staircase", [r.get("id") for r in staircase_rooms],
                                "EXPLICIT_INPUT", "required_specific", "rooms", "type=staircase"))
        else:
            missing.append(_missing("STAIRCASE_INFO", "Staircase identity and dimensions",
                                     "required_specific", blocking=True,
                                     required_for=["geometry_validation", "practical_review"]))
    else:
        if staircase_rooms:
            facts.append(_fact("STAIRCASE_INFO", "Staircase (single-story, informational)",
                                [r.get("id") for r in staircase_rooms],
                                "EXPLICIT_INPUT", "optional", "rooms", "type=staircase"))
        else:
            facts.append(_fact("STAIRCASE_INFO", "Staircase (single-story, none provided)",
                                None, "EXPLICIT_INPUT", "optional", "requirements", "single_story_only=true"))

    # ---- Source provenance ----
    if isinstance(input_provenance, dict):
        src_kind = input_provenance.get("source_kind", "manual_json")
        facts.append(_fact("SOURCE_PROVENANCE", "Input provenance kind", src_kind,
                            "EXPLICIT_INPUT", "required_specific", "input_provenance", "source_kind"))
        if input_provenance.get("source_document_checksum"):
            facts.append(_fact("SOURCE_CHECKSUM", "Source document checksum",
                                input_provenance.get("source_document_checksum"),
                                "EXPLICIT_INPUT", "required_specific", "input_provenance",
                                "source_document_checksum"))
        if src_kind == "reviewed_drawing":
            for fact_id, label in _reviewed_drawing_provenance_missing(input_provenance):
                missing.append(
                    _missing(
                        fact_id,
                        label,
                        "required_specific",
                        blocking=True,
                        required_for=["provenance_tracking", "architect_pilot_gate"],
                    )
                )
    else:
        missing.append(_missing("SOURCE_PROVENANCE", "Source provenance / document checksum",
                                 "required_specific", blocking=True,
                                 required_for=["provenance_tracking", "architect_pilot_gate"]))

    # ---- Optional facts ----
    if brahmasthan:
        facts.append(_fact("BRAHMASTHAN_TREATMENT", "Brahmasthan treatment",
                            brahmasthan.get("type"),
                            "EXPLICIT_INPUT", "optional", "brahmasthan_treatment", "type"))

    if services:
        facts.append(_fact("SERVICES_INFO", "Services summary",
                            {k: v for k, v in services.items() if v},
                            "EXPLICIT_INPUT", "optional", "services", "keys"))

    # ---- Inferred: room count ----
    inferred.append(_inferred("ROOM_COUNT", "Total room count", len(rooms), "high",
                               "required_practical",
                               note="Counted from rooms array."))

    return facts, missing, inferred


def extract_facts_from_validation_report(data: dict[str, Any]) -> tuple[list[Fact], list[MissingItem], list[InferredItem]]:
    """Extract facts from a phase1 validation report."""
    facts: list[Fact] = []
    missing: list[MissingItem] = []
    inferred: list[InferredItem] = []

    plot_summary = data.get("plot_summary", {})

    if plot_summary:
        facts.append(_fact("PROJECT_NAME", "Project name", data.get("project_name"),
                            "EXPLICIT_INPUT", "required_geometry", "root", "project_name"))
        facts.append(_fact("PLOT_DIMENSIONS", "Plot dimensions",
                            {"width_ft": plot_summary.get("width_ft"), "depth_ft": plot_summary.get("depth_ft")},
                            "EXPLICIT_INPUT", "required_geometry", "plot_summary", "width_ft, depth_ft"))
        if plot_summary.get("width_ft") and plot_summary.get("depth_ft"):
            inferred.append(_inferred("PLOT_AREA", "Plot area (sq ft)",
                                       round(plot_summary["width_ft"] * plot_summary["depth_ft"], 2),
                                       "high", "required_geometry",
                                       note="Derived from plot_summary dimensions."))
        facts.append(_fact("ORIENTATION_FACING", "Plot facing", plot_summary.get("facing"),
                            "EXPLICIT_INPUT", "required_geometry", "plot_summary", "facing"))
        facts.append(_fact("ROAD_SIDE", "Road side", plot_summary.get("road_side"),
                            "EXPLICIT_INPUT", "required_geometry", "plot_summary", "road_side"))
        facts.append(_fact("VALIDATION_STATUS", "Validation status", data.get("validation_status"),
                            "EXPLICIT_INPUT", "required_practical", "root", "validation_status"))
        facts.append(_fact("ROOM_COUNT", "Room count", data.get("room_count"),
                            "EXPLICIT_INPUT", "required_practical", "root", "room_count"))
        facts.append(_fact("PARKING_COUNT", "Parking count", data.get("parking_count"),
                            "EXPLICIT_INPUT", "required_specific", "root", "parking_count"))
    else:
        missing.append(_missing("PLOT_DIMENSIONS", "Plot dimensions", "required_geometry", blocking=True,
                                 required_for=["geometry_validation"]))
        missing.append(_missing("ORIENTATION_FACING", "Plot facing", "required_geometry", blocking=True,
                                 required_for=["geometry_validation"]))
        missing.append(_missing("SOURCE_PROVENANCE", "Source provenance", "required_specific", blocking=False,
                                 required_for=["provenance_tracking"]))

    issues = data.get("issues", [])
    if issues:
        facts.append(_fact("VALIDATION_ISSUES", "Validation issues", len(issues),
                            "EXPLICIT_INPUT", "required_practical", "root", "issues"))

    provisional = data.get("provisional_notes", [])
    if provisional:
        facts.append(_fact("PROVISIONAL_NOTES", "Provisional notes", len(provisional),
                            "EXPLICIT_INPUT", "optional", "root", "provisional_notes"))

    return facts, missing, inferred


# ---------------------------------------------------------------------------
# Readiness evaluation
# ---------------------------------------------------------------------------


def evaluate_review_readiness(
    facts: list[Fact],
    missing: list[MissingItem],
    inferred: list[InferredItem],
    blocking_items: list[BlockingItem],
) -> ValidationReadiness:
    """Evaluate whether the input is ready for preliminary review."""
    required_geometry_fact_ids = {f.fact_id for f in facts
                                   if f.category == "required_geometry" and f.evidence_type != "MISSING_INPUT"}
    required_practical_fact_ids = {f.fact_id for f in facts
                                    if f.category == "required_practical" and f.evidence_type != "MISSING_INPUT"}

    blocking_geometry = [m for m in missing
                         if m.category == "required_geometry" and m.blocking]
    blocking_practical = [m for m in missing
                          if m.category == "required_practical" and m.blocking]
    blocking_specific = [m for m in missing
                         if m.category == "required_specific" and m.blocking]
    inferred_blocking = [i for i in inferred if i.blocking]

    geometry_ready = len(blocking_geometry) == 0
    practical_ready = len(blocking_practical) == 0 and len(blocking_specific) == 0

    if blocking_items or blocking_geometry or blocking_specific:
        blocking_labels = [bi.label for bi in blocking_items]
        blocking_labels += [m.label for m in blocking_geometry + blocking_specific]
        reason = "Required information is missing: " + "; ".join(blocking_labels[:5])
        if len(blocking_labels) > 5:
            reason += f" and {len(blocking_labels) - 5} more."
        return ValidationReadiness(
            result="NOT_READY_MISSING_REQUIRED_INPUT",
            reason=reason,
            geometry_ready=geometry_ready,
            practical_ready=practical_ready,
        )

    if inferred_blocking or blocking_practical:
        labels = [i.label for i in inferred_blocking] + [m.label for m in blocking_practical]
        return ValidationReadiness(
            result="READY_WITH_LIMITATIONS",
            reason="Some required fields are inferred or incomplete: " + "; ".join(labels[:5]),
            geometry_ready=geometry_ready,
            practical_ready=len(blocking_practical) == 0,
            limitations=[i.label for i in inferred_blocking] + [m.label for m in blocking_practical],
        )

    return ValidationReadiness(
        result="READY_FOR_PRELIMINARY_REVIEW",
        reason="All required geometry and practical-review information is present.",
        geometry_ready=True,
        practical_ready=True,
    )


# ---------------------------------------------------------------------------
# Blocking / non-blocking classification
# ---------------------------------------------------------------------------


def classify_blocking(missing: list[MissingItem], inferred: list[InferredItem]) -> tuple[list[BlockingItem], list[BlockingItem]]:
    """Separate missing and inferred items into blocking and non-blocking."""
    blocking: list[BlockingItem] = []
    non_blocking: list[BlockingItem] = []

    for m in missing:
        item = _blocking(m.fact_id, m.label,
                         f"Missing required input: {m.label}. Category: {m.category}.",
                         related=[])
        if m.blocking:
            blocking.append(item)
        else:
            non_blocking.append(item)

    for inf in inferred:
        item = _non_blocking(inf.fact_id, inf.label,
                              f"Inferred value ({inf.confidence} confidence): {inf.derivation_note or 'see derivation note'}.",
                              related=[])
        if inf.blocking:
            blocking.append(item)
        else:
            non_blocking.append(item)

    return blocking, non_blocking


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_confirmation_package(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    replace: bool = False,
    include_timestamp: bool = False,
    product_checkpoint: str = PRODUCT_CHECKPOINT,
) -> Path:
    """Create a confirmation package from a structured input file.

    Parameters
    ----------
    input_path : Path to the source input file (structured JSON).
    output_dir : Directory where the confirmation package will be written.
    replace : If True, overwrite an existing package. If False, raise on conflict.
    include_timestamp : If True, include generated_at timestamp. Per project
        conventions, timestamps are omitted unless explicitly allowed.
    product_checkpoint : Product checkpoint identity to record.

    Returns
    -------
    Path to the written JSON package.
    """
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    package_name = f"{input_path.stem}_confirmation"
    json_path = output_dir / f"{package_name}.json"
    md_path = output_dir / f"{package_name}.md"

    if json_path.exists() and not replace:
        raise FileExistsError(
            f"Confirmation package already exists at {json_path}. Use --replace to overwrite."
        )

    sha256 = _sha256_file(input_path)
    data, input_kind = _load_input(input_path)

    if input_kind == "floor_plan":
        facts, missing, inferred = extract_facts_from_floor_plan(data)
    elif input_kind == "validation_report":
        facts, missing, inferred = extract_facts_from_validation_report(data)
    else:
        # Generic fallback: extract what we can
        facts = [_fact("SOURCE_PROVENANCE", "Input kind", input_kind,
                        "EXPLICIT_INPUT", "required_specific", "root", "input_kind")]
        missing = [_missing("PLOT_DIMENSIONS", "Plot dimensions", "required_geometry",
                             blocking=True, required_for=["geometry_validation"])]
        inferred = []

    blocking_items, non_blocking_items = classify_blocking(missing, inferred)

    # Build correction history from any pre-existing architect_responses in input
    architect_responses: list[ResponseItem] = []

    total_facts = len(facts) + len(inferred)
    readiness = evaluate_review_readiness(facts, missing, inferred, blocking_items)

    summary = CompletenessSummary(
        required_fields_present=readiness.geometry_ready and readiness.practical_ready,
        optional_fields_present=sum(1 for f in facts if f.category == "optional"),
        total_facts=total_facts,
        missing_count=len(missing),
        inferred_count=len(inferred),
        blocking_count=len(blocking_items),
    )

    # Determine overall_status from responses
    response_statuses = [r.status for r in architect_responses]
    if not response_statuses:
        overall_status = "UNREVIEWED"
    else:
        # If all responses are CONFIRMED or NOT_APPLICABLE, status is CONFIRMED
        # If any CORRECTED, status is CORRECTED
        # If any UNKNOWN, status is UNKNOWN
        if all(s in ("CONFIRMED", "NOT_APPLICABLE") for s in response_statuses):
            overall_status = "CONFIRMED"
        elif any(s == "CORRECTED" for s in response_statuses):
            overall_status = "CORRECTED"
        elif any(s == "UNKNOWN" for s in response_statuses):
            overall_status = "UNKNOWN"
        else:
            overall_status = "UNREVIEWED"

    package = InputConfirmation(
        source_input_path=str(input_path),
        source_input_sha256=sha256,
        generated_at=datetime.now(timezone.utc) if include_timestamp else None,
        product_checkpoint_used=product_checkpoint,
        overall_status=overall_status,
        completeness_summary=summary,
        extracted_facts=facts,
        missing_information=missing,
        inferred_information=inferred,
        architect_responses=architect_responses,
        blocking_items=blocking_items,
        non_blocking_items=non_blocking_items,
        validation_readiness=readiness,
    )

    # Atomic write
    tmp_json = json_path.with_suffix(".tmp")
    tmp_json.write_text(package.model_dump_json(indent=2), encoding="utf-8")
    tmp_json.replace(json_path)

    from knowledge_engine.intake.confirmation_renderer import render_markdown
    md_text = render_markdown(package)
    tmp_md = md_path.with_suffix(".tmp")
    tmp_md.write_text(md_text, encoding="utf-8")
    tmp_md.replace(md_path)

    return json_path


def apply_confirmation_responses(
    package_path: str | Path,
    responses: ResponseBatch,
) -> Path:
    """Apply architect confirmation responses to an existing package.

    Parameters
    ----------
    package_path : Path to an existing input_confirmation.json.
    responses : Validated ResponseBatch with architect responses.

    Returns
    -------
    Path to the updated JSON package.
    """
    package_path = Path(package_path)
    raw = package_path.read_text(encoding="utf-8")
    data = json.loads(raw)
    package = InputConfirmation.model_validate(data)

    # Validate response fact_ids against known facts
    known_ids = {f.fact_id for f in package.extracted_facts}
    known_ids |= {m.fact_id for m in package.missing_information}
    known_ids |= {i.fact_id for i in package.inferred_information}

    for response in responses.responses:
        if response.fact_id not in known_ids:
            raise ValueError(f"Unknown fact_id: {response.fact_id}")

    # Apply corrections
    for response in responses.responses:
        if response.status == "CORRECTED":
            # Find and update the matching fact
            for fact in package.extracted_facts:
                if fact.fact_id == response.fact_id:
                    fact.provenance.derivation_note = (
                        f"Corrected by architect. Original: {fact.value}. "
                        f"Corrected: {response.corrected_value}."
                    )
                    # Store original before overwriting
                    response.original_value = _serialize(fact.value)
                    fact.value = response.corrected_value
                    break

    # Re-evaluate readiness after corrections
    package.architect_responses = list(responses.responses)
    package.overall_status = _derive_overall_status(package.architect_responses)

    # Re-check blocking after corrections
    new_blocking: list[BlockingItem] = []
    new_non_blocking: list[BlockingItem] = []
    for m in package.missing_information:
        item = _blocking(m.fact_id, m.label,
                         f"Missing required input: {m.label}. Category: {m.category}.",
                         related=[])
        if m.blocking:
            new_blocking.append(item)
        else:
            new_non_blocking.append(item)
    for inf in package.inferred_information:
        item = _non_blocking(inf.fact_id, inf.label,
                              f"Inferred value ({inf.confidence} confidence).",
                              related=[])
        if inf.blocking:
            new_blocking.append(item)
        else:
            new_non_blocking.append(item)
    package.blocking_items = new_blocking
    package.non_blocking_items = new_non_blocking

    package.validation_readiness = evaluate_review_readiness(
        package.extracted_facts, package.missing_information,
        package.inferred_information, package.blocking_items,
    )
    package.completeness_summary.blocking_count = len(package.blocking_items)

    # Atomic write
    tmp = package_path.with_suffix(".tmp")
    tmp.write_text(package.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(package_path)

    return package_path


def _derive_overall_status(responses: list[ResponseItem]) -> str:
    if not responses:
        return "UNREVIEWED"
    statuses = [r.status for r in responses]
    if all(s in ("CONFIRMED", "NOT_APPLICABLE") for s in statuses):
        return "CONFIRMED"
    if any(s == "CORRECTED" for s in statuses):
        return "CORRECTED"
    if any(s == "UNKNOWN" for s in statuses):
        return "UNKNOWN"
    return "UNREVIEWED"


def _serialize(value: Any) -> Any:
    """Convert a value to a JSON-safe representation for provenance tracking."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, dict)):
        return value
    return str(value)


def evaluate_readiness(package: InputConfirmation) -> ValidationReadiness:
    """Re-evaluate readiness for an existing package."""
    return evaluate_review_readiness(
        package.extracted_facts,
        package.missing_information,
        package.inferred_information,
        package.blocking_items,
    )
