"""Deterministic completion of explicitly requested planning spaces."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _room_bounds(room: dict[str, Any]) -> tuple[float, float, float, float] | None:
    polygon = room.get("polygon") or []
    if len(polygon) < 4:
        return None
    xs = [float(point[0]) for point in polygon]
    ys = [float(point[1]) for point in polygon]
    return min(xs), min(ys), max(xs), max(ys)


def _overlaps(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> bool:
    left_x1, left_y1, left_x2, left_y2 = left
    right_x1, right_y1, right_x2, right_y2 = right
    return not (
        left_x2 <= right_x1
        or right_x2 <= left_x1
        or left_y2 <= right_y1
        or right_y2 <= left_y1
    )


def _find_free_rectangle(
    occupied: list[tuple[float, float, float, float]],
    plot_width: float,
    plot_depth: float,
    width: float,
    height: float,
    *,
    step: float = 0.5,
) -> tuple[float, float, float, float] | None:
    if width > plot_width or height > plot_depth:
        return None
    y = 0.0
    while y <= plot_depth - height + 0.001:
        x = 0.0
        while x <= plot_width - width + 0.001:
            candidate = (x, y, x + width, y + height)
            if not any(_overlaps(candidate, other) for other in occupied):
                return candidate
            x = round(x + step, 3)
        y = round(y + step, 3)
    return None


def _canonical_room(
    template: dict[str, Any],
    room_id: str,
    room_type: str,
    bounds: tuple[float, float, float, float],
) -> dict[str, Any]:
    x1, y1, x2, y2 = bounds
    room = deepcopy(template)
    room.update(
        {
            "id": room_id,
            "type": room_type,
            "polygon": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
            "area_sqft": round((x2 - x1) * (y2 - y1), 2),
            "legacy_metadata": {
                "original_area_sqft": round((x2 - x1) * (y2 - y1), 2),
                "source_record_id": None,
                "requirement_id": None,
                "creation_lineage": "room_program_completion",
            },
            "compatibility": {
                "bbox": {
                    "x": x1,
                    "y": y1,
                    "w": x2 - x1,
                    "h": y2 - y1,
                    "derived": True,
                    "exact_representation": True,
                }
            },
            "windows": [],
            "door": None,
            "ventilation_met": False,
            "creation_lineage": "room_program_completion",
        }
    )
    return room


def complete_room_program(
    plan: dict[str, Any],
    *,
    bathrooms: int,
    requires_parking: bool,
    requires_pooja: bool,
) -> tuple[dict[str, Any], list[str]]:
    """Add requested program spaces only when collision-free rectangles exist."""

    completed = deepcopy(plan)
    rooms = completed.setdefault("rooms", [])
    parking = completed.setdefault("parking", [])
    plot = completed.get("plot", {})
    plot_width = float(plot.get("width_ft", plot.get("width", 0)))
    plot_depth = float(plot.get("depth_ft", plot.get("depth", 0)))
    occupied = [bounds for room in rooms if (bounds := _room_bounds(room))]
    occupied.extend(
        (float(item["x"]), float(item["y"]), float(item["x"]) + float(item["width"]), float(item["y"]) + float(item["height"]))
        for item in parking
    )
    blockers: list[str] = []
    template = rooms[0] if rooms else {
        "floor": 0,
        "storey_id": "ground",
        "level_id": "ground",
        "identity": {},
        "geometry_revision": 1,
    }

    existing_bathrooms = sum("bathroom" in str(room.get("type", "")).lower() or "toilet" in str(room.get("type", "")).lower() for room in rooms)
    next_id = len(rooms) + 1
    for index in range(existing_bathrooms, bathrooms):
        bounds = _find_free_rectangle(occupied, plot_width, plot_depth, 5.0, 5.0)
        if bounds is None:
            blockers.append(f"required bathrooms={bathrooms}, generated={existing_bathrooms}")
            break
        rooms.append(_canonical_room(template, f"R{next_id}", f"Bathroom {index + 1}", bounds))
        occupied.append(bounds)
        existing_bathrooms += 1
        next_id += 1

    if requires_pooja and not any("pooja" in str(room.get("type", "")).lower() for room in rooms):
        bounds = _find_free_rectangle(occupied, plot_width, plot_depth, 5.0, 5.0)
        if bounds is None:
            blockers.append("required pooja room was not generated")
        else:
            rooms.append(_canonical_room(template, f"R{next_id}", "Pooja Room", bounds))
            occupied.append(bounds)
            next_id += 1

    if requires_parking and not parking:
        bounds = _find_free_rectangle(occupied, plot_width, plot_depth, 9.0, 18.0)
        if bounds is None:
            blockers.append("required parking was not generated")
        else:
            x1, y1, x2, y2 = bounds
            parking.append(
                {
                    "id": "P1",
                    "x": x1,
                    "y": y1,
                    "width": x2 - x1,
                    "height": y2 - y1,
                    "level": 0,
                    "vehicle_type": "car",
                    "inside_plot": True,
                    "notes": ["provisional deterministic placement"],
                }
            )

    completed["room_program_completion"] = {
        "status": "blocked" if blockers else "complete",
        "added_room_types": [
            str(room.get("type"))
            for room in rooms
            if room.get("creation_lineage") == "room_program_completion"
        ],
        "parking_count": len(parking),
    }
    return completed, blockers
