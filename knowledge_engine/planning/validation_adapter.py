"""Convert provisional planner output into the deterministic validation boundary."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from knowledge_engine.domain.floor_plan import FloorPlanSchema
from knowledge_engine.validation.pipeline import run_validation_pipeline

from .planner_models import DraftPlanRequest
from .usability import assess_architect_usability


def _room_bounds(room: dict[str, Any]) -> tuple[float, float, float, float]:
    polygon = room.get("polygon") or []
    if len(polygon) < 4:
        raise ValueError(f"room {room.get('id', '<unknown>')} has no rectangular polygon")
    xs = [float(point[0]) for point in polygon]
    ys = [float(point[1]) for point in polygon]
    return min(xs), min(ys), max(xs), max(ys)


def _room_type(value: Any) -> str:
    normalized = str(value or "other").strip().lower()
    if "master bedroom" in normalized:
        return "master_bedroom"
    if "bedroom" in normalized:
        return "bedroom"
    if "bathroom" in normalized or "toilet" in normalized:
        return "bathroom"
    if "living" in normalized:
        return "living"
    if "kitchen" in normalized:
        return "kitchen"
    if "pooja" in normalized:
        return "pooja"
    if "dining" in normalized:
        return "dining"
    return normalized if normalized in {
        "staircase", "utility", "parking", "circulation", "other"
    } else "other"


def canonical_to_floor_plan_data(
    canonical: dict[str, Any],
    request: DraftPlanRequest,
) -> dict[str, Any]:
    """Build additive JSON compatible with ``FloorPlanSchema``."""

    plot = canonical.get("plot", {})
    rooms: list[dict[str, Any]] = []
    for room in canonical.get("rooms", []):
        x1, y1, x2, y2 = _room_bounds(room)
        door = room.get("door") or {}
        windows = room.get("windows") or []
        rooms.append(
            {
                "id": str(room.get("id")),
                "type": _room_type(room.get("type")),
                "name": str(room.get("type") or room.get("id")),
                "x": x1,
                "y": y1,
                "width": round(x2 - x1, 3),
                "height": round(y2 - y1, 3),
                "area": round((x2 - x1) * (y2 - y1), 2),
                "level": int(room.get("floor", 0)),
                "doors": [f"{door.get('wall', 'unknown')} door"] if door else [],
                "windows": [f"{item.get('wall', 'unknown')} window" for item in windows],
                "notes": ["provisional planner output"],
            }
        )

    return {
        "metadata": {
            "plan_id": str(canonical.get("plan_id") or "planner-draft"),
            "project_name": request.project_name,
            "source_prompt": request.source_prompt,
            "units": "ft",
            "level_count": request.floors,
            "schema_version": "0.1.0",
        },
        "plot": {
            "width_ft": float(plot.get("width_ft", plot.get("width", request.plot_width_ft))),
            "depth_ft": float(plot.get("depth_ft", plot.get("depth", request.plot_depth_ft))),
            "facing": request.facing,
            "road_side": request.road_side or request.facing,
            "north_angle_deg": 0.0,
        },
        "requirements": {
            "bhk": request.bhk,
            "required_bedrooms_count": request.bhk,
            "requires_parking": request.requires_parking,
            "requires_pooja": request.requires_pooja,
            "single_story_only": request.floors == 1,
            "required_room_types": [],
        },
        "rooms": rooms,
        "openings": [],
        "parking": list(canonical.get("parking") or []),
        "services": dict(canonical.get("services") or {}),
        "notes": ["Generated as a provisional planner draft; validate before review."],
    }


def validate_generated_draft(
    canonical: dict[str, Any],
    request: DraftPlanRequest,
) -> dict[str, Any]:
    """Validate a ready draft in memory and skip blocked drafts fail-closed."""

    if canonical.get("generation_status") == "blocked":
        return {
            "generation_status": "blocked",
            "validation_skipped": True,
            "blocked_reason": canonical.get("blocked_reason"),
            "report": None,
            "scorecard": None,
        }

    payload = canonical_to_floor_plan_data(canonical, request)
    with tempfile.TemporaryDirectory(prefix="knowledge_engine_draft_") as directory:
        input_path = Path(directory) / "draft.json"
        input_path.write_text(json.dumps(payload), encoding="utf-8")
        result = run_validation_pipeline(input_path, Path(directory) / "outputs", write_outputs=False)
    report = result["report"]
    scorecard = result["scorecard"]
    plan = FloorPlanSchema.model_validate(payload)
    assessment = assess_architect_usability(plan, report)
    return {
        "generation_status": "draft",
        "validation_skipped": False,
        "validation_status": report.validation_status,
        "report": report,
        "scorecard": scorecard,
        "plan_data": payload,
        "architect_usability": assessment.to_dict(),
    }
