"""Narrow deterministic templates for supported architect-review drafts."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .planner_models import DraftPlanRequest


def _room(
    room_id: str,
    room_type: str,
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    door_wall: str | None,
    window_wall: str | None = None,
) -> dict[str, Any]:
    room: dict[str, Any] = {
        "id": room_id,
        "type": room_type,
        "floor": 0,
        "polygon": [[x, y], [x + width, y], [x + width, y + height], [x, y + height]],
        "area_sqft": width * height,
        "identity": {},
        "geometry_revision": 1,
    }
    if door_wall:
        room["door"] = {"wall": door_wall, "offset_ft": round((height if door_wall in {"E", "W"} else width) / 2, 1), "swings_into": room_id}
    if window_wall:
        room["windows"] = [{"wall": window_wall, "offset_ft": round((height if window_wall in {"E", "W"} else width) / 2, 1), "width_ft": 3.0}]
    return room


def build_30x40_east_3bhk_template(request: DraftPlanRequest) -> dict[str, Any] | None:
    """Return the supported compact 30x40 east-road 3BHK template, or ``None``.

    This is a deterministic review draft rather than an optimizer. Its geometry
    is intentionally explicit so the architect-usability gate can be tested
    without reducing room sizes to make a generic scan-grid layout fit.
    """
    if not (
        request.floors == 1
        and request.bhk == 3
        and request.bathrooms == 3
        and request.requires_parking
        and request.requires_pooja
        and request.road_side_or_facing.lower() == "east"
        and abs(request.plot_width_ft - 30.0) < 0.01
        and abs(request.plot_depth_ft - 40.0) < 0.01
    ):
        return None

    brief = deepcopy(request.to_project_brief())
    brief["template_id"] = "compact_30x40_east_3bhk_v1"
    brief["template_status"] = "provisional_architect_review_draft"
    brief["rooms"] = [
        _room("R1", "Living Room", 0.0, 0.0, 12.0, 13.0, door_wall="W", window_wall="W"),
        _room("R2", "Kitchen", 20.0, 0.0, 10.0, 8.0, door_wall="S", window_wall="E"),
        _room("R3", "Ground Floor Bathroom", 12.0, 28.0, 5.0, 5.0, door_wall="S", window_wall="N"),
        _room("R4", "Master Bedroom", 0.0, 28.0, 12.0, 12.0, door_wall="S", window_wall="W"),
        _room("R5", "Bedroom 2", 20.0, 10.0, 10.0, 10.0, door_wall="W", window_wall="E"),
        _room("R6", "Bedroom 3", 0.0, 14.0, 10.0, 10.0, door_wall="E", window_wall="W"),
        _room("R7", "Bathroom 2", 17.0, 28.0, 5.0, 5.0, door_wall="S", window_wall="N"),
        _room("R8", "Bathroom 3", 20.0, 20.0, 5.0, 5.0, door_wall="N", window_wall="E"),
        _room("R9", "Pooja Room", 5.0, 24.0, 5.0, 4.0, door_wall="S", window_wall="W"),
        _room("R10", "Circulation", 10.0, 13.333, 10.0, 13.334, door_wall=None),
    ]
    brief["parking"] = [
        {
            "id": "P1",
            "x": 22.0,
            "y": 26.0,
            "width": 8.0,
            "height": 14.0,
            "level": 0,
            "vehicle_type": "car",
            "inside_plot": True,
            "notes": ["provisional template parking with east road-side access"],
        }
    ]
    return brief
