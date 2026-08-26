from __future__ import annotations

import json
from pathlib import Path

from knowledge_engine.domain import FloorPlanSchema
from knowledge_engine.validation.geometry import validate_geometry
from knowledge_engine.validation.practical import validate_practical
from knowledge_engine.validation.requirements import validate_requirements
from knowledge_engine.validation.zones import classify_room_zone
from knowledge_engine.validation.rectangles import Rect


SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "valid_minimal_plan.json"


def _load_plan() -> FloorPlanSchema:
    return FloorPlanSchema.model_validate(json.loads(SAMPLE.read_text(encoding="utf-8")))


def test_validation_modules_accept_domain_floor_plan() -> None:
    plan = _load_plan()
    geometry_issues, geometry_data = validate_geometry(plan)
    requirement_issues = validate_requirements(plan)
    practical_issues, _ = validate_practical(plan)

    assert isinstance(geometry_issues, list)
    assert isinstance(geometry_data, dict)
    assert isinstance(requirement_issues, list)
    assert isinstance(practical_issues, list)


def test_zone_tagging_uses_local_rectangle_primitive() -> None:
    assert classify_room_zone(30.0, 40.0, Rect(0.0, 0.0, 10.0, 10.0)) == "south_west"
