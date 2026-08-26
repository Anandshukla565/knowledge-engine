import json
import re
from copy import deepcopy
from datetime import datetime
from typing import Any

from knowledge_engine.ai.gemini_client import run_llm_prompt
from knowledge_engine.planning.geometry_model import build_canonical_model
from knowledge_engine.planning.geometry_model import legacy_state_to_canonical_model
from knowledge_engine.planning.geometry_model import _resolve_identity
from knowledge_engine.planning.geometry_model import _deterministic_id
from knowledge_engine.planning.geometry_model import _polygon_area
from knowledge_engine.planning.commercial import build_commercial_floor_spec, is_commercial_space_type, COMMERCIAL_ADJACENCY_RULES
from knowledge_engine.planning.planner_adapter import (
    AdapterResult,
    ClassifiedConstraint,
    ConstraintClassification,
    planner_constraints_as_rules,
)




MINIMUM_ROOM_SIZES = {
    # Standard 2BHK
    "MasterBed": {"w": 12.0, "h": 12.0, "area": 144.0},
    "Master Bedroom": {"w": 12.0, "h": 12.0, "area": 144.0},
    "Bedroom": {"w": 10.0, "h": 10.0, "area": 100.0},
    "Bedroom 2": {"w": 10.0, "h": 10.0, "area": 100.0},
    "Bedroom 3": {"w": 10.0, "h": 10.0, "area": 100.0},
    "Bedroom 4": {"w": 10.0, "h": 10.0, "area": 100.0},
    "Bedroom 5": {"w": 10.0, "h": 10.0, "area": 100.0},
    "Bedroom 6": {"w": 10.0, "h": 10.0, "area": 100.0},
    "Kitchen": {"w": 8.0, "h": 10.0, "area": 80.0},
    "Living": {"w": 12.0, "h": 14.0, "area": 168.0},
    "Living Room": {"w": 12.0, "h": 14.0, "area": 168.0},
    "Bathroom": {"w": 5.0, "h": 8.0, "area": 40.0},
    "Toilet": {"w": 5.0, "h": 8.0, "area": 40.0},
    "Staircase": {"w": 5.0, "h": 8.0, "area": 40.0},
    "Stairs": {"w": 5.0, "h": 8.0, "area": 40.0},
    # Villa / Luxury
    "Master Suite":        {"w": 18.0, "h": 20.0, "area": 360.0},
    "Walk-in Closet":      {"w": 6.0,  "h": 8.0,  "area": 48.0},
    "Dressing Room":       {"w": 8.0,  "h": 8.0,  "area": 64.0},
    "Drawing Room":        {"w": 14.0, "h": 16.0, "area": 224.0},
    "Formal Living":       {"w": 16.0, "h": 18.0, "area": 288.0},
    "Family Lounge":       {"w": 14.0, "h": 16.0, "area": 224.0},
    "Dining Room":         {"w": 12.0, "h": 14.0, "area": 168.0},
    "Pooja Room":          {"w": 6.0,  "h": 8.0,  "area": 48.0},
    "Servant Room":        {"w": 8.0,  "h": 10.0, "area": 80.0},
    "Servant's Room":      {"w": 8.0,  "h": 10.0, "area": 80.0},
    "Home Theater":        {"w": 16.0, "h": 20.0, "area": 320.0},
    "Home Theatre":        {"w": 16.0, "h": 20.0, "area": 320.0},
    "Gym":                 {"w": 12.0, "h": 16.0, "area": 192.0},
    "Study":               {"w": 10.0, "h": 12.0, "area": 120.0},
    "Utility":             {"w": 6.0,  "h": 8.0,  "area": 48.0},
    "Laundry":             {"w": 6.0,  "h": 8.0,  "area": 48.0},
    "Store":               {"w": 5.0,  "h": 6.0,  "area": 30.0},
    "Balcony":             {"w": 8.0,  "h": 10.0, "area": 80.0},
    "Verandah":            {"w": 10.0, "h": 12.0, "area": 120.0},
    "Open Terrace":        {"w": 1.0,  "h": 1.0,  "area": 0.0},
    "Covered Terrace":     {"w": 8.0,  "h": 10.0, "area": 80.0},
    "Multi-car Garage":    {"w": 20.0, "h": 20.0, "area": 400.0},
    "Double Garage":       {"w": 14.0, "h": 20.0, "area": 280.0},
    "Lift":                {"w": 6.0,  "h": 6.0,  "area": 36.0},
    "Foyer":               {"w": 6.0,  "h": 8.0,  "area": 48.0},
    "Passage":             {"w": 4.0,  "h": 10.0, "area": 40.0},
    "Guest Bedroom":       {"w": 10.0, "h": 12.0, "area": 120.0},
    "Attached Bathroom":   {"w": 5.0,  "h": 8.0,  "area": 40.0},
    "Common Bathroom":     {"w": 5.0,  "h": 8.0,  "area": 40.0},
    "Powder Room":         {"w": 3.0,  "h": 5.0,  "area": 15.0},
}

# ---- Plot-size classification constants ----

# Plot area (sqft) below which we warn about tight layouts but still proceed.
_TINY_PLOT_AREA = 750.0       # ~25×30 ft
# Plot area at which minimum room sizes start being relaxed.
_RELAX_PLOT_AREA = 1200.0     # ~30×40 ft
# Scale factor at which a room is considered "cramped but buildable".
_MIN_SCALE_FACTOR = 0.55
# Plot area above which we warn about oversized rooms.
_LARGE_PLOT_AREA = 6400.0     # ~80×80 ft
# Max scale-up before we cap and distribute excess as gaps.
_MAX_SCALE_FACTOR = 1.30
# Minimum viable room area (sqft) — rooms below this are flagged as compromises.
_MIN_VIABLE_AREA = 80.0
# Number of "special" room types that, when all present simultaneously, may crowd the layout.
_SPECIAL_ROOM_TYPES = {
    "Home Theater", "Home Theatre", "Gym", "Pooja Room",
    "Study", "Dressing Room", "Walk-in Closet", "Utility", "Laundry",
}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _next_version(project_state: dict) -> float:
    current = _as_float(project_state.get("version"), 1.0)
    return round(current + 0.1, 2)


def _plot_dimensions(project_state: dict) -> tuple[float, float]:
    plot = project_state.get("plot", {})
    width = _as_float(plot.get("usable_width_ft") or plot.get("width_ft"))
    depth = _as_float(plot.get("usable_depth_ft") or plot.get("depth_ft"))
    if width <= 0 or depth <= 0:
        raise ValueError("Plot usable dimensions are required before spatial planning.")
    return width, depth


def _minimum_for_room(room_type: str, plot_area: float = 3600.0) -> dict | None:
    """Return minimum size for a room, relaxed if the plot is too small.

    On plots smaller than _RELAX_PLOT_AREA (1200 sqft), minimum areas are
    scaled down proportionally so rooms can still fit — they'll just be
    flagged as compromises.
    """
    base = _minimum_for_room_base(room_type)
    if base is None:
        return None
    if plot_area >= _RELAX_PLOT_AREA:
        return base
    # Scale down minimum area proportionally
    relaxed_ratio = max(0.45, plot_area / _RELAX_PLOT_AREA)
    relaxed = {
        "w": max(4.0, round(base["w"] * relaxed_ratio, 1)),
        "h": max(4.0, round(base["h"] * relaxed_ratio, 1)),
        "area": max(25.0, round(base["area"] * relaxed_ratio, 1)),
    }
    return relaxed


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _next_version(project_state: dict) -> float:
    current = _as_float(project_state.get("version"), 1.0)
    return round(current + 0.1, 2)


def _plot_dimensions(project_state: dict) -> tuple[float, float]:
    plot = project_state.get("plot", {})
    width = _as_float(plot.get("usable_width_ft") or plot.get("width_ft"))
    depth = _as_float(plot.get("usable_depth_ft") or plot.get("depth_ft"))
    if width <= 0 or depth <= 0:
        raise ValueError("Plot usable dimensions are required before spatial planning.")
    return width, depth


def _brahmasthan_bounds(plot_w: float, plot_d: float) -> dict:
    cell_w = plot_w / 3.0
    cell_d = plot_d / 3.0
    return {"x": cell_w, "y": cell_d, "w": cell_w, "h": cell_d, "area_sqft": cell_w * cell_d}


def _minimum_for_room_base(room_type: str) -> dict | None:
    """Original minimum size lookup (no relaxation)."""
    if room_type in MINIMUM_ROOM_SIZES:
        return MINIMUM_ROOM_SIZES[room_type]
    normalized = room_type.lower().replace("_", " ").strip()
    if "master suite" in normalized or ("master" in normalized and "suite" in normalized):
        return MINIMUM_ROOM_SIZES["Master Suite"]
    if "walk" in normalized and "closet" in normalized:
        return MINIMUM_ROOM_SIZES["Walk-in Closet"]
    if "dressing" in normalized:
        return MINIMUM_ROOM_SIZES["Dressing Room"]
    if "drawing" in normalized:
        return MINIMUM_ROOM_SIZES["Drawing Room"]
    if "formal" in normalized and "living" in normalized:
        return MINIMUM_ROOM_SIZES["Formal Living"]
    if "family" in normalized and "lounge" in normalized:
        return MINIMUM_ROOM_SIZES["Family Lounge"]
    if "dining" in normalized:
        return MINIMUM_ROOM_SIZES["Dining Room"]
    if "pooja" in normalized:
        return MINIMUM_ROOM_SIZES["Pooja Room"]
    if "servant" in normalized:
        return MINIMUM_ROOM_SIZES["Servant Room"]
    if "home theater" in normalized or "home theatre" in normalized:
        return MINIMUM_ROOM_SIZES["Home Theater"]
    if "gym" in normalized:
        return MINIMUM_ROOM_SIZES["Gym"]
    if "study" in normalized and "bed" not in normalized:
        return MINIMUM_ROOM_SIZES["Study"]
    if normalized == "utility":
        return MINIMUM_ROOM_SIZES["Utility"]
    if "laundry" in normalized:
        return MINIMUM_ROOM_SIZES["Laundry"]
    if "balcony" in normalized:
        return MINIMUM_ROOM_SIZES["Balcony"]
    if "verandah" in normalized:
        return MINIMUM_ROOM_SIZES["Verandah"]
    if "open terrace" in normalized:
        return MINIMUM_ROOM_SIZES["Open Terrace"]
    if "covered terrace" in normalized:
        return MINIMUM_ROOM_SIZES["Covered Terrace"]
    if "multi-car" in normalized or "multi car" in normalized:
        return MINIMUM_ROOM_SIZES["Multi-car Garage"]
    if "double garage" in normalized or "garage" in normalized:
        return MINIMUM_ROOM_SIZES["Double Garage"]
    if "lift" in normalized:
        return MINIMUM_ROOM_SIZES["Lift"]
    if "foyer" in normalized:
        return MINIMUM_ROOM_SIZES["Foyer"]
    if "passage" in normalized:
        return MINIMUM_ROOM_SIZES["Passage"]
    if "guest" in normalized and "bed" in normalized:
        return MINIMUM_ROOM_SIZES["Guest Bedroom"]
    if "attached" in normalized and ("bath" in normalized or "toilet" in normalized):
        return MINIMUM_ROOM_SIZES["Attached Bathroom"]
    if "common" in normalized and ("bath" in normalized or "toilet" in normalized):
        return MINIMUM_ROOM_SIZES["Common Bathroom"]
    if "powder" in normalized:
        return MINIMUM_ROOM_SIZES["Powder Room"]
    if "master" in normalized and "bed" in normalized:
        return MINIMUM_ROOM_SIZES["MasterBed"]
    if "bed" in normalized:
        return MINIMUM_ROOM_SIZES["Bedroom"]
    if "kitchen" in normalized:
        return MINIMUM_ROOM_SIZES["Kitchen"]
    if "living" in normalized:
        return MINIMUM_ROOM_SIZES["Living"]
    if "bath" in normalized or "toilet" in normalized:
        return MINIMUM_ROOM_SIZES["Bathroom"]
    if "stair" in normalized:
        return MINIMUM_ROOM_SIZES["Staircase"]
    return None


# ---------------------------------------------------------------------------
# Plot-size constraint analysis
# ---------------------------------------------------------------------------


def _analyze_plot_constraints(project_state: dict) -> list[dict]:
    """Analyze plot size vs. requested rooms and return a list of constraint
    compromises (warnings, not hard failures).

    Each entry has:
      type:       one of "tiny_plot", "large_plot", "many_special_rooms",
                  "single_floor_too_many", "plot_shape_irregular"
      severity:   "warning" | "info"
      details:    human-readable explanation
      suggestion: optional string with a concrete workaround
    """
    plot = project_state.get("plot", {})
    requirements = project_state.get("requirements", {})
    special_rooms = requirements.get("special_rooms", [])
    warnings: list[dict] = []

    # ---- Dimensions ----
    width = _as_float(plot.get("usable_width_ft") or plot.get("width_ft"))
    depth = _as_float(plot.get("usable_depth_ft") or plot.get("depth_ft"))
    plot_area = width * depth

    # ---- Tiny plots ----
    if plot_area > 0 and plot_area <= _TINY_PLOT_AREA:
        min_total = sum(
            (v.get("area") or 0)
            for v in MINIMUM_ROOM_SIZES.values()
            if v.get("area", 0) > 0
        )
        # Only count the rooms we'll actually generate
        floors = int(requirements.get("floors") or 1)
        bedrooms = int(requirements.get("bedrooms") or 2)
        bathrooms = int(requirements.get("bathrooms") or 1)
        special_count = len(special_rooms)
        estimated_rooms = 4 + bedrooms + bathrooms + special_count  # living, kitchen, bath per floor, staircase
        estimated_area = sum(v["area"] for v in list(MINIMUM_ROOM_SIZES.values())[:estimated_rooms] if v["area"] > 0)
        scale = plot_area / estimated_area if estimated_area > 0 else 0
        warnings.append({
            "type": "tiny_plot",
            "severity": "warning",
            "details": (
                f"Plot area {plot_area:.0f} sqft ({width:.0f}x{depth:.0f} ft) is small. "
                f"Rooms will need to scale down (~{scale:.0%} of standard size). "
                "Minimum room sizes will be relaxed with compromise flags."
            ),
            "suggestion": "Consider adding a floor or reducing special rooms.",
        })

    # ---- Large plots ----
    elif plot_area > _LARGE_PLOT_AREA:
        warnings.append({
            "type": "large_plot",
            "severity": "info",
            "details": (
                f"Plot area {plot_area:.0f} sqft is large. "
                f"Rooms will scale up but capped at {_MAX_SCALE_FACTOR:.0%}. "
                "Excess space will be left as gaps between rooms."
            ),
            "suggestion": "Consider adding a garden, parking, or additional rooms.",
        })

    # ---- Many special rooms ----
    special_present = [r for r in special_rooms if any(
        kw in r.lower() for kw in ("theater", "gym", "pooja", "study", "dressing", "walk-in", "utility", "laundry")
    )]
    if len(special_present) >= 3:
        warnings.append({
            "type": "many_special_rooms",
            "severity": "warning",
            "details": (
                f"{len(special_present)} special rooms requested ({', '.join(special_present[:3])}…). "
                "Layout will be tight — rooms may share walls or be undersized."
            ),
            "suggestion": "Consider placing special rooms on upper floors or combining spaces.",
        })

    # ---- Single-floor with too many rooms ----
    floors = int(requirements.get("floors") or 1)
    bedrooms = int(requirements.get("bedrooms") or 2)
    bathrooms = int(requirements.get("bathrooms") or 1)
    if floors == 1 and bedrooms >= 5:
        warnings.append({
            "type": "single_floor_too_many",
            "severity": "warning",
            "details": (
                f"{bedrooms} bedrooms on a single floor requires {bedrooms + 3} rooms minimum "
                f"(bedrooms + living + kitchen + bathroom). Layout will be very compact."
            ),
            "suggestion": "Consider G+1 or more floors for comfortable spacing.",
        })

    # ---- Irregular plot shape ----
    boundary = plot.get("boundary_coords")
    if boundary and len(boundary) > 4:
        warnings.append({
            "type": "plot_shape_irregular",
            "severity": "warning",
            "details": (
                f"Irregular plot shape ({len(boundary)} boundary points detected). "
                "Engine assumes rectangular plots — actual usable area may differ."
            ),
            "suggestion": "Review room placement manually; some rooms may extend beyond usable area.",
        })

    return warnings


# ---------------------------------------------------------------------------
# Room-size scaling helpers
# ---------------------------------------------------------------------------


def _recommended_scale(project_state: dict) -> float:
    """Return a scale factor for room dimensions based on plot size.

    For normal plots this returns ~1.0.  For very small plots it returns
    a value < 1.0 (e.g. 0.55 for 20×25).  For very large plots it returns
    a capped value > 1.0 (max 1.30).
    """
    plot = project_state.get("plot", {})
    width = _as_float(plot.get("usable_width_ft") or plot.get("width_ft"))
    depth = _as_float(plot.get("usable_depth_ft") or plot.get("depth_ft"))
    plot_area = width * depth

    if plot_area >= _LARGE_PLOT_AREA:
        # Scale up but cap
        raw = (plot_area / 3600.0) ** 0.5  # 3600 = 60×60 reference
        return min(raw, _MAX_SCALE_FACTOR)
    if plot_area <= _TINY_PLOT_AREA:
        # Scale down but don't go below minimum
        raw = (plot_area / 3600.0) ** 0.5
        return max(raw, _MIN_SCALE_FACTOR)
    # Linear interpolation between tiny and normal
    if plot_area < 3600.0:
        t = (plot_area - _TINY_PLOT_AREA) / (3600.0 - _TINY_PLOT_AREA)
        return max(_MIN_SCALE_FACTOR, _MIN_SCALE_FACTOR + t * (1.0 - _MIN_SCALE_FACTOR))
    return 1.0


def _rect(room: dict) -> tuple[float, float, float, float]:
    x = _as_float(room.get("x"))
    y = _as_float(room.get("y"))
    w = _as_float(room.get("w"))
    h = _as_float(room.get("h"))
    return x, y, x + w, y + h


def _rects_overlap(a: dict, b: dict) -> bool:
    ax1, ay1, ax2, ay2 = _rect(a)
    bx1, by1, bx2, by2 = _rect(b)
    return ax1 < bx2 and ax2 > bx1 and ay1 < by2 and ay2 > by1


def _room_overlaps_brahmasthan(room: dict, brahmasthan: dict) -> bool:
    return _rects_overlap(room, {"x": brahmasthan["x"], "y": brahmasthan["y"], "w": brahmasthan["w"], "h": brahmasthan["h"]})


# ---------------------------------------------------------------------------
# Suite Grouping System
# ---------------------------------------------------------------------------
# Suites keep related rooms together with fixed relative positions.
# A suite is placed as a single unit, then expanded into individual rooms.
# Each entry: suite_name -> {
#   "rooms": {room_type: (rel_x, rel_y, w, h)},
#   "preferred_zones": [(row, col), ...] in the 3x3 grid,
#   "min_plot_side": minimum plot side in feet for this suite
# }
# ---------------------------------------------------------------------------

SUITE_TEMPLATES: dict[str, dict] = {
    "Master Suite": {
        "description": "Master bedroom + walk-in + dressing + en-suite bathroom",
        "min_plot_side": 40.0,
        "width": 22.0,
        "height": 22.0,
        "rooms": {
            "Master Bedroom":  (0.0,  0.0, 14.0, 14.0),
            "Walk-in Closet":  (0.0, 14.0,  6.0,  8.0),
            "Dressing Room":   (6.0, 14.0,  8.0,  8.0),
            "Master Bathroom": (14.0, 0.0,  8.0, 10.0),
        },
        "preferred_zones": [(2, 0)],  # SW
    },
    "Guest Suite": {
        "description": "Guest bedroom + attached bathroom",
        "min_plot_side": 24.0,
        "width": 12.0,
        "height": 20.0,
        "rooms": {
            "Guest Bedroom":   (0.0,  0.0, 12.0, 12.0),
            "Attached Bathroom": (6.0, 12.0, 6.0, 8.0),
        },
        "preferred_zones": [(2, 1)],  # S
    },
    "Formal Living Block": {
        "description": "Drawing room + dining room + pooja (ground floor public zone)",
        "min_plot_side": 36.0,
        "width": 24.0,
        "height": 28.0,
        "rooms": {
            "Drawing Room":  (0.0,  0.0, 16.0, 14.0),
            "Dining Room":   (0.0, 14.0, 14.0, 14.0),
            "Pooja Room":    (16.0, 0.0,  8.0,  8.0),
        },
        "preferred_zones": [(0, 0), (0, 1)],  # NW / N
    },
    "Family Block": {
        "description": "Family lounge + home theater + balcony (upper floor private zone)",
        "min_plot_side": 40.0,
        "width": 34.0,
        "height": 22.0,
        "rooms": {
            "Family Lounge":  (0.0,  0.0, 16.0, 14.0),
            "Home Theater":   (16.0, 0.0, 18.0, 14.0),
            "Balcony":        (0.0, 14.0, 20.0,  8.0),
        },
        "preferred_zones": [(0, 1), (0, 2)],  # N / NE
    },
    "Service Block": {
        "description": "Servant room + utility + store + laundry (ground floor service zone)",
        "min_plot_side": 20.0,
        "width": 16.0,
        "height": 18.0,
        "rooms": {
            "Servant Room": (0.0,  0.0, 10.0, 10.0),
            "Laundry":      (10.0, 0.0,  6.0,  8.0),
            "Utility":      (0.0, 10.0,  8.0,  8.0),
            "Store":        (8.0, 10.0,  8.0,  8.0),
        },
        "preferred_zones": [(2, 2)],  # SE
    },
}

# Map from individual room type to the suite it belongs to (for grouping)
SUITE_MEMBERSHIP: dict[str, str] = {}
for suite_name, suite_def in SUITE_TEMPLATES.items():
    for room_type in suite_def["rooms"]:
        SUITE_MEMBERSHIP[room_type] = suite_name


def _suite_bounds(suite_def: dict) -> tuple[float, float]:
    """Return (width, height) of a suite's bounding box."""
    max_x = max(rx + rw for rx, _, rw, _ in suite_def["rooms"].values())
    max_y = max(ry + rh for _, ry, _, rh in suite_def["rooms"].values())
    return max_x, max_y


def _expand_suite(suite_name: str, suite_def: dict, origin_x: float, origin_y: float, floor: int) -> list[dict]:
    """Expand a suite template into individual room dicts at the given origin.

    Raises ValueError if any expanded rooms overlap (suite template bug)
    or exceed the bounding box (placement bug). Templates should be
    hand-verified non-overlapping rectangles within the declared suite size.
    """
    rooms = []
    for room_type, (rx, ry, rw, rh) in suite_def["rooms"].items():
        rooms.append({
            "id": None,
            "type": room_type,
            "floor": floor,
            "x": round(origin_x + rx, 1),
            "y": round(origin_y + ry, 1),
            "w": rw,
            "h": rh,
            "area_sqft": round(rw * rh, 2),
            "suite_group": suite_name,
        })
    # Sanity: rooms within declared template bounds
    declared_w = suite_def.get("width")
    declared_h = suite_def.get("height")
    for room in rooms:
        local_x = room["x"] - origin_x
        local_y = room["y"] - origin_y
        if declared_w is not None and (local_x + room["w"]) > declared_w + 0.5:
            raise ValueError(
                f"Suite {suite_name}: room {room['type']} exceeds template width "
                f"(local_x={local_x}, w={room['w']}, declared={declared_w})"
            )
        if declared_h is not None and (local_y + room["h"]) > declared_h + 0.5:
            raise ValueError(
                f"Suite {suite_name}: room {room['type']} exceeds template height "
                f"(local_y={local_y}, h={room['h']}, declared={declared_h})"
            )
    # Sanity: no overlaps between expanded rooms of same suite
    for i, a in enumerate(rooms):
        for b in rooms[i + 1:]:
            if (a["x"] < b["x"] + b["w"] and a["x"] + a["w"] > b["x"] and
                    a["y"] < b["y"] + b["h"] and a["y"] + a["h"] > b["y"]):
                raise ValueError(
                    f"Suite {suite_name}: rooms {a['type']} and {b['type']} overlap "
                    f"in template definition"
                )
    return rooms


_EPSILON = 0.05  # small tolerance for floating-point drift in boundary checks
_MIN_CLEARANCE = 2.0  # minimum circulation gap between any two rooms (feet)


def _is_wet_area(room_type: str) -> bool:
    """Return True if room_type is a plumbing/wet area that needs vertical stacking."""
    wet_keywords = ("bathroom", "toilet", "kitchen", "laundry", "attached bathroom",
                    "common bathroom", "master bathroom", "powder room", "ground floor bathroom")
    normalized = room_type.lower()
    return any(kw in normalized for kw in wet_keywords)


def _clearance_between(a: dict, b: dict) -> float:
    """Return minimum edge-to-edge gap between two rectangles, or 0 if they overlap."""
    ax1, ay1, ax2, ay2 = _rect(a)
    bx1, by1, bx2, by2 = _rect(b)
    # No overlap → return minimum gap
    if ax2 <= bx1:
        return bx1 - ax2
    if bx2 <= ax1:
        return ax1 - bx2
    if ay2 <= by1:
        return by1 - ay2
    if by2 <= ay1:
        return ay1 - by2
    return 0.0  # overlapping


def _check_clearances(rooms: list[dict]) -> list[str]:
    """Return list of clearance violation messages for rooms on the same floor."""
    errors: list[str] = []
    by_floor: dict[int, list[dict]] = {}
    for room in rooms:
        floor = int(room.get("floor", 0))
        by_floor.setdefault(floor, []).append(room)
    for floor, floor_rooms in sorted(by_floor.items()):
        for i, a in enumerate(floor_rooms):
            for b in floor_rooms[i + 1:]:
                gap = _clearance_between(a, b)
                if 0 <= gap < _MIN_CLEARANCE:
                    aid = a.get("id", f"idx{i}")
                    bid = b.get("id", f"idx{floor_rooms.index(b)}")
                    errors.append(
                        f"Insufficient clearance ({gap:.1f}ft < {_MIN_CLEARANCE}ft) "
                        f"between {aid} ({a.get('type')}) and {bid} ({b.get('type')}) on floor {floor}."
                    )
    return errors


def _check_wet_area_stacking(rooms: list[dict]) -> list[str]:
    """Warn when wet-area rooms don't vertically align across floors."""
    warnings: list[str] = []
    by_floor: dict[int, list[dict]] = {}
    for room in rooms:
        floor = int(room.get("floor", 0))
        by_floor.setdefault(floor, []).append(room)
    floors = sorted(by_floor.keys())
    if len(floors) < 2:
        return warnings
    for idx in range(1, len(floors)):
        prev_floor = floors[idx - 1]
        curr_floor = floors[idx]
        prev_wet = {(round(r["x"], 1), round(r["y"], 1), round(r["w"], 1), round(r["h"], 1))
                    for r in by_floor[prev_floor] if _is_wet_area(r.get("type", ""))}
        curr_wet = {(round(r["x"], 1), round(r["y"], 1), round(r["w"], 1), round(r["h"], 1))
                    for r in by_floor[curr_floor] if _is_wet_area(r.get("type", ""))}
        if prev_wet and curr_wet:
            matched = sum(1 for pw in prev_wet if any(
                abs(pw[0] - cw[0]) < 2.0 and abs(pw[1] - cw[1]) < 2.0
                for cw in curr_wet))
            if matched == 0:
                warnings.append(
                    f"No wet-area vertical alignment between floor {prev_floor} and floor {curr_floor}."
                )
    return warnings
_MIN_CLEARANCE = 2.0  # minimum circulation gap between any two rooms (feet)


def _validate_rooms(rooms: list[dict], plot_w: float, plot_d: float, brahmasthan: dict,
                    space_type: str = "residential",
                    preserved_walls: list[dict] | None = None) -> list[str]:
    errors: list[str] = []
    seen_ids: set[str] = set()
    for index, room in enumerate(rooms):
        room_id = str(room.get("id") or f"index_{index}")
        room_type = str(room.get("type") or "").strip()
        x = _as_float(room.get("x"))
        y = _as_float(room.get("y"))
        w = _as_float(room.get("w"))
        h = _as_float(room.get("h"))
        if not room_id:
            errors.append(f"Room at index {index} is missing id.")
        if room_id in seen_ids:
            errors.append(f"Duplicate room id: {room_id}")
        seen_ids.add(room_id)
        if not room_type:
            errors.append(f"{room_id} is missing type.")
        if w <= 0 or h <= 0:
            errors.append(f"{room_id} has invalid dimensions.")
        if x < -_EPSILON or y < -_EPSILON or x + w > plot_w + _EPSILON or y + h > plot_d + _EPSILON:
            errors.append(f"{room_id} exceeds usable plot boundary.")
        minimum = _minimum_for_room(room_type, plot_w * plot_d)
        if minimum and (w * h) < minimum["area"]:
            # Don't fail — log as a compromise (already below-min from planner shrink).
            # The actual minimum-area gate only applies when both dimensions
            # match the template (i.e. not intentionally shrunk).
            area_ratio = (w * h) / minimum["area"] if minimum["area"] > 0 else 0
            room.setdefault("compromise_reason", "")
            if not room.get("compromise_reason"):
                room["compromise_reason"] = (
                    f"Below minimum size ({w*h:.0f} sqft vs {minimum['area']} sqft, "
                    f"{area_ratio:.0%} of target) — needed to fit on available plot"
                )
        if _requires_window(room_type) and len(room.get("windows", [])) < 1:
            # Only flag if the room actually has exterior walls (interior rooms
            # surrounded by other rooms can't have windows and are accepted).
            ext_walls = _exterior_walls(room, plot_w, plot_d)
            if ext_walls:
                errors.append(f"{room_id} ({room_type}) has no windows on exterior walls.")
        # Check preserved-wall overlaps
        if preserved_walls:
            rx, ry, rw, rh = x, y, w, h
            for wall in preserved_walls:
                if _wall_blocks_room(rx, ry, rw, rh, wall):
                    errors.append(
                        f"{room_id} overlaps preserved wall {wall.get('id', '?')}"
                    )
    for i, room_a in enumerate(rooms):
        for room_b in rooms[i + 1:]:
            if int(room_a.get("floor", 0)) != int(room_b.get("floor", 0)):
                continue
            if _rects_overlap(room_a, room_b):
                errors.append(f"{room_a.get('id')} overlaps {room_b.get('id')}.")
    return errors


def _villa_room_size(room_type: str) -> tuple[float, float]:
    """Return (width, height) for a room type used in villa-mode placement.

    This is the single authoritative size lookup for villa floors.  When a
    room type is added to (or removed from) SUITE_TEMPLATES, its size only
    needs to be added here — every floor that references it picks it up
    automatically.
    """
    sizes: dict[str, tuple[float, float]] = {
        # Standalone / utility rooms
        "Foyer":           (6.0, 8.0),
        "Living Room":     (12.0, 14.0),
        "Kitchen":         (10.0, 14.0),
        "Powder Room":     (3.0, 5.0),
        "Verandah":        (10.0, 12.0),
        "Staircase":       (6.0, 8.0),
        "Gym":             (12.0, 16.0),
        "Open Terrace":    (8.0, 10.0),
        "Common Bathroom": (5.0, 8.0),
        "Attached Bathroom": (5.0, 8.0),
        # Formal Living Block
        "Drawing Room":    (14.0, 16.0),
        "Dining Room":     (12.0, 14.0),
        "Pooja Room":      (6.0, 8.0),
        "Formal Living":   (16.0, 18.0),
        # Guest Suite
        "Guest Bedroom":   (10.0, 12.0),
        # Master Suite
        "Master Bedroom":  (12.0, 14.0),
        "Master Suite":    (18.0, 20.0),
        "Walk-in Closet":  (6.0, 8.0),
        "Dressing Room":   (8.0, 8.0),
        # Family Block
        "Family Lounge":   (14.0, 16.0),
        "Home Theater":    (16.0, 20.0),
        "Balcony":         (8.0, 10.0),
        # Service Block
        "Servant's Room":  (8.0, 10.0),
        "Laundry":         (6.0, 8.0),
        "Utility":         (6.0, 8.0),
        "Store":           (5.0, 6.0),
        "Covered Terrace": (8.0, 10.0),
    }
    return sizes.get(room_type, _numeric_bedroom_default(room_type, (10.0, 12.0)))


_NUMERIC_BEDROOM_RE = re.compile(r"^Bedroom\s+\d+$", re.IGNORECASE)

def _numeric_bedroom_default(room_type: str, fallback: tuple[float, float]) -> tuple[float, float]:
    """Return *fallback* for dynamically-labelled rooms like "Bedroom 5"."""
    if _NUMERIC_BEDROOM_RE.match(room_type):
        return fallback
    return fallback


def _build_villa_floor_specs(
    plot_w: float,
    plot_d: float,
    requirements: dict,
) -> dict[int, list]:
    """Build per-floor room specs for villa mode, sourced from SUITE_TEMPLATES.

    Each floor's composition is derived by iterating the relevant suites'
    room lists.  Room sizes come from ``_villa_room_size`` so that adding
    a room type to a suite in ``SUITE_TEMPLATES`` only requires one extra
    entry in ``_villa_room_size`` — every floor that references it is
    automatically updated.
    """
    bedrooms = int(requirements.get("bedrooms", 2))
    floors = int(requirements.get("floors", 1))
    attached_baths = int(requirements.get("attached_bathrooms", bedrooms))
    large_plot = plot_w >= 55.0 and plot_d >= 55.0

    specs: dict[int, list] = {}

    # ── Floor 0: Formal Living Block + Guest Suite + Service Block (partial) + standalone ──
    f0: list = []
    for suite_name in ("Formal Living Block", "Guest Suite"):
        for room_type in SUITE_TEMPLATES[suite_name]["rooms"]:
            f0.append((room_type, *_villa_room_size(room_type)))
    for room_type in ("Store", "Utility"):          # Service Block partial
        f0.append((room_type, *_villa_room_size(room_type)))
    for room_type in ("Foyer", "Living Room", "Formal Living", "Kitchen", "Powder Room", "Verandah", "Staircase"):
        f0.append((room_type, *_villa_room_size(room_type)))
    if attached_baths >= 1:
        f0.append(("Attached Bathroom", *_villa_room_size("Attached Bathroom")))
    f0.append(("Common Bathroom", *_villa_room_size("Common Bathroom")))
    specs[0] = f0

    # ── Floor 1: Master Suite + Family Block (partial) + standalone ──
    f1: list = [
        ("Master Bedroom", *_villa_room_size("Master Bedroom")),
        ("Family Lounge",  *_villa_room_size("Family Lounge")),
        ("Staircase",      *_villa_room_size("Staircase")),
    ]
    if large_plot:
        f1.extend([
            ("Master Suite",   *_villa_room_size("Master Suite")),
            ("Walk-in Closet", *_villa_room_size("Walk-in Closet")),
            ("Dressing Room",  *_villa_room_size("Dressing Room")),
        ])
    extra_beds = min(bedrooms - 2, 3)
    for i in range(max(0, extra_beds)):
        f1.append((f"Bedroom {i + 2}", *_villa_room_size("Bedroom 2")))
    f1.append(("Attached Bathroom", *_villa_room_size("Attached Bathroom")))
    f1.append(("Common Bathroom", *_villa_room_size("Common Bathroom")))
    f1.append(("Balcony", *_villa_room_size("Balcony")))
    specs[1] = f1

    # ── Floor 2: Family Block (partial) + Service Block (partial) + standalone ──
    f2: list = [
        ("Staircase",      *_villa_room_size("Staircase")),
        ("Home Theater",   *_villa_room_size("Home Theater")),
        ("Gym",            *_villa_room_size("Gym")),
        ("Servant's Room", *_villa_room_size("Servant's Room")),
        ("Laundry",        *_villa_room_size("Laundry")),
        ("Covered Terrace",*_villa_room_size("Covered Terrace")),
    ]
    f2.append(("Common Bathroom", *_villa_room_size("Common Bathroom")))
    if bedrooms >= 4:
        f2.append(("Bedroom 5", *_villa_room_size("Bedroom 5")))
    if bedrooms >= 6:
        f2.append(("Bedroom 6", *_villa_room_size("Bedroom 6")))
    specs[2] = f2

    # ── Floor 3+: partial Service Block + standalone ──
    if floors >= 4:
        f3: list = [
            ("Staircase",    *_villa_room_size("Staircase")),
            ("Open Terrace", *_villa_room_size("Open Terrace")),
            ("Utility",      *_villa_room_size("Utility")),
        ]
        if bedrooms >= 4:
            f3.extend([
                ("Bedroom 6",       *_villa_room_size("Bedroom 6")),
                ("Attached Bathroom", *_villa_room_size("Attached Bathroom")),
            ])
        if bedrooms >= 5:
            f3.append(("Bedroom 7", *_villa_room_size("Bedroom 7")))
        specs[3] = f3

    return specs


def _normalize_room(room: dict, index: int) -> dict:
    x = _as_float(room.get("x"))
    y = _as_float(room.get("y"))
    w = _as_float(room.get("w"))
    h = _as_float(room.get("h"))
    vertices = room.get("vertices")
    if vertices:
        # Recompute area from vertices via shoelace when present.
        try:
            n = len(vertices)
            if n >= 3:
                s = 0.0
                for i in range(n):
                    x_i, y_i = vertices[i]
                    x_j, y_j = vertices[(i + 1) % n]
                    s += x_i * y_j - x_j * y_i
                area_sqft = round(abs(s) / 2.0, 2)
            else:
                area_sqft = round(w * h, 2)
        except Exception:
            area_sqft = round(w * h, 2)
    else:
        area_sqft = round(w * h, 2)
    result = {
        "id": str(room.get("id") or f"R{index + 1}"),
        "type": str(room.get("type") or "Room"),
        "floor": int(room.get("floor", 0)),
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "area_sqft": area_sqft,
        "suite_group": room.get("suite_group"),
        "placement_zone": room.get("placement_zone"),
        "alignment_status": room.get("alignment_status", "unchecked"),
        "alignment_rule_applied": room.get("alignment_rule_applied"),
        "compromise_reason": room.get("compromise_reason"),
        "remedy": room.get("remedy"),
    }
    # Preserve constraint trace metadata when present
    if "constraint_ids_applied" in room:
        result["constraint_ids_applied"] = room["constraint_ids_applied"]
    if vertices:
        result["vertices"] = vertices
    return result


def _extract_json_array(text: str) -> list[dict]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1 or end <= start:
            raise
        data = json.loads(raw[start:end + 1])
    if not isinstance(data, list):
        raise ValueError("Gemini response must be a JSON array.")
    if not all(isinstance(item, dict) for item in data):
        raise ValueError("Every room entry must be a JSON object.")
    return data


def _build_prompt(project_state: dict, brahmasthan: dict, previous_error: str | None = None) -> str:
    plot = project_state.get("plot", {})
    requirements = project_state.get("requirements", {})
    plot_w, plot_d = _plot_dimensions(project_state)
    floors = int(requirements.get("floors", 1))
    prompt = {
        "task": "Create initial rectangular room placements only. Do not check Vastu. Do not explain.",
        "hard_rules": [
            "Return ONLY a JSON array of room objects.",
            "No markdown.",
            "No prose.",
            "No comments.",
            "All coordinates are in feet from usable plot origin.",
            "Do not exceed usable plot boundary.",
            "Do not overlap rooms on the same floor.",
            "Rooms on different floors may reuse the same x/y coordinates.",
            "For floors >= 2, distribute mandatory rooms across ground floor (0) and upper floors as needed.",
            "Avoid Brahmasthan overlap when possible, but do not omit mandatory rooms to satisfy it.",
            f"Floor 0 = ground floor. Floor 1 = first floor. Floor 2 = second floor. Floor 3 = third floor. Use floor numbers 0 through {floors - 1}.",
        ],
        "usable_plot": {"width_ft": plot_w, "depth_ft": plot_d, "facing": plot.get("facing"), "setbacks": plot.get("setbacks", {})},
        "requirements": requirements,
        "minimum_room_sizes": MINIMUM_ROOM_SIZES,
        "brahmasthan_boundary": brahmasthan,
        "vastu_zones": project_state.get("vastu_zones", {}),
        "vastu_zone_preferences": {
            "Kitchen": "SE zone preferred. NE, N, and C are forbidden.",
            "Master Bedroom": "SW zone preferred.",
            "Master Suite": "SW zone preferred.",
            "Living Room": "N or NE zone preferred.",
            "Formal Living": "N or NE zone preferred.",
            "Drawing Room": "N or NE zone preferred.",
            "Family Lounge": "NW or N zone preferred.",
            "Dining Room": "W or SW zone preferred.",
            "Bathroom": "NW or W zone preferred. Never NE.",
            "Attached Bathroom": "NW or W zone preferred. Never NE.",
            "Common Bathroom": "NW or W zone preferred. Never NE.",
            "Powder Room": "NW or W zone preferred.",
            "Staircase": "S or SW zone preferred. Never NE or center.",
            "Bedroom 2": "NW or W zone preferred.",
            "Bedroom 3": "NW or W zone preferred.",
            "Bedroom 4": "NW or W zone preferred.",
            "Bedroom 5": "NW or W zone preferred.",
            "Bedroom 6": "NW or W zone preferred.",
            "Guest Bedroom": "W or S zone preferred.",
            "Pooja Room": "NE zone preferred.",
            "Home Theater": "N or NE zone preferred.",
            "Gym": "W or NW zone preferred.",
            "Study": "W or NW zone preferred.",
            "Servant's Room": "S or SW zone preferred.",
            "Servant Room": "S or SW zone preferred.",
            "Laundry": "S or SE zone preferred.",
            "Utility": "S or SW zone preferred.",
            "Store": "S or SW zone preferred.",
            "Walk-in Closet": "W zone preferred.",
            "Dressing Room": "W zone preferred.",
            "Foyer": "N zone preferred.",
            "Lift": "C or S zone.",
            "Verandah": "N or NE zone preferred.",
            "Balcony": "Any zone.",
            "Open Terrace": "Any zone.",
            "Covered Terrace": "N or W zone preferred.",
            "Multi-car Garage": "S or SW zone preferred.",
            "Double Garage": "S or SW zone preferred.",
        },
        "vastu_hard_constraints": {
            "Kitchen": {
                "forbidden_zones": ["NE", "N", "C"],
                "reason": "NE is sacred water zone. Kitchen fire element destroys NE energy. This is non-negotiable.",
            },
            "Bathroom": {
                "forbidden_zones": ["NE", "C"],
                "reason": "NE must stay clean and open. Waste water in NE is critical violation.",
            },
            "Attached Bathroom": {
                "forbidden_zones": ["NE", "C"],
                "reason": "NE must stay clean and open.",
            },
            "Common Bathroom": {
                "forbidden_zones": ["NE", "C"],
                "reason": "NE must stay clean and open.",
            },
            "Powder Room": {
                "forbidden_zones": ["NE", "C"],
                "reason": "NE must stay clean and open.",
            },
            "Toilet": {
                "forbidden_zones": ["NE", "C"],
                "reason": "NE must stay clean and open. Waste water in NE is critical violation.",
            },
            "Staircase": {
                "forbidden_zones": ["NE", "C"],
                "reason": "Staircase in NE or center is critical violation.",
            },
            "Laundry": {
                "forbidden_zones": ["NE", "C"],
                "reason": "Waste water drains in NE or center are critical violations.",
            },
        },
        "vastu_hard_constraint_instruction": (
            "The forbidden_zones above are ABSOLUTE. If a room is listed with a forbidden zone, "
            "it CANNOT be placed there under any circumstance. Find another location even if it "
            "means reducing room size slightly. Violation of forbidden zones will cause plan rejection."
        ),
        "required_rooms_checklist": {
            "living_room": "MANDATORY - must include",
            "kitchen": "MANDATORY - must include",
            "master_bedroom": "MANDATORY - must include",
            "bedroom_2": "MANDATORY if bedrooms >= 2",
            "bathroom": f"MANDATORY - need {requirements.get('bathrooms', 1)}",
            "staircase": "MANDATORY if floors >= 2",
            "pooja_room": "RECOMMENDED - pooja room in NE is Vastu preferred",
            "dining_room": "RECOMMENDED - near kitchen",
            "guest_bedroom": "RECOMMENDED for villas (3+ floors or 5+ bedrooms)",
            "master_suite": "For villas: master suite = bedroom + walk-in closet + dressing + attached bath in SW",
            "servant_room": "RECOMMENDED for villas",
            "home_theater": "OPTIONAL - upper floor entertainment",
            "gym": "OPTIONAL - upper floor recreation",
            "study": "OPTIONAL - near master bedroom",
            "utility_laundry": "RECOMMENDED - near kitchen or service area",
            "balcony": "RECOMMENDED - attached to bedrooms and living",
            "verandah": "RECOMMENDED - ground floor front",
            "terrace": "OPTIONAL - top floor open terrace",
            "parking": "MANDATORY if requires_parking",
            "lift": "REQUIRED if 3+ floors and plot >= 50x50",
            "foyer": "RECOMMENDED for villas - entry transition space",
            "passage": "MANDATORY - circulation path connecting rooms",
        },
        "warning": "If ANY mandatory room is missing your response will be rejected and you must retry",
        "required_room_object_schema": {
            "id": "R1",
            "type": "Living/Kitchen/MasterBed/Bedroom/Bathroom/Staircase/etc",
            "floor": 0,
            "x": 0.0,
            "y": 0.0,
            "w": 0.0,
            "h": 0.0,
            "area_sqft": 0.0,
            "placement_zone": None,
            "alignment_status": "unchecked",
            "alignment_rule_applied": None,
            "compromise_reason": None,
            "remedy": None,
        },
    }
    if previous_error:
        prompt["previous_response_failed_because"] = previous_error
        prompt["retry_instruction"] = "Fix the layout and return only a valid JSON array."
    if project_state.get("hard_vastu_retry_violations"):
        prompt["previous_hard_vastu_violations"] = project_state["hard_vastu_retry_violations"]
        prompt["retry_instruction"] = (
            "Regenerate the full layout. The listed hard Vastu violations are not acceptable. "
            "Move those rooms out of forbidden zones and return only a valid JSON array."
        )
    return json.dumps(prompt, indent=2)


def _call_llm(prompt: str) -> str:
    return run_llm_prompt(prompt, timeout_seconds=240)



def _check_required_rooms(rooms: list, requirements: dict) -> list:
    missing = []
    room_types = [r.get("type", "").lower() for r in rooms]
    floor_rooms: dict[int, list[str]] = {}
    for room in rooms:
        floor = int(room.get("floor", 0))
        floor_rooms.setdefault(floor, []).append(room.get("type", "").lower())

    has_living = any("living" in t for t in room_types)
    if not has_living:
        missing.append("Living Room")

    has_kitchen = any("kitchen" in t for t in room_types)
    kitchen_count = sum(1 for t in room_types if "kitchen" in t)
    required_kitchens = int(requirements.get("kitchens", 1))
    if kitchen_count < required_kitchens:
        missing.append(f"{required_kitchens} Kitchens (only {kitchen_count} found)")

    bed_count = sum(1 for t in room_types if "bed" in t or "suite" in t)
    required_beds = int(requirements.get("bedrooms", 2))
    if bed_count < required_beds:
        missing.append(f"{required_beds} Bedrooms (only {bed_count} found)")

    bath_count = sum(1 for t in room_types if "bath" in t or "toilet" in t)
    required_baths = int(requirements.get("bathrooms", 1))
    if bath_count < required_baths:
        missing.append(f"{required_baths} Bathrooms (only {bath_count} found)")

    floors = int(requirements.get("floors", 1))

    # Check entrance requirements
    entrances = requirements.get("entrances", []) or []
    if "main" in entrances:
        has_foyer = any("foyer" in t for t in room_types)
        has_living = any("living" in t for t in room_types)
        if not has_foyer and not has_living:
            missing.append("Main entrance: Foyer room missing")
    if "service" in entrances:
        if not any("service" in t for t in room_types):
            missing.append("Service entrance: Service Room missing")
    if floors >= 2:
        ground = floor_rooms.get(0, [])
        if not any("living" in t for t in ground):
            missing.append("Ground floor Living Room")
        if not any("kitchen" in t for t in ground):
            missing.append("Ground floor Kitchen")
        if not any("bath" in t or "toilet" in t for t in ground):
            missing.append("Ground floor Bathroom")

    # Check each upper floor has at least 1 bedroom (for 3-4 floor villas)
    bedroom_floor_labels = {1: "First", 2: "Second", 3: "Third", 4: "Fourth"}
    for f in range(1, floors):
        label = bedroom_floor_labels.get(f, f"Floor {f}")
        frooms = floor_rooms.get(f, [])
        fbed = sum(1 for t in frooms if "bed" in t or "suite" in t)
        # Require at least 1 bedroom per floor; on 2-floor buildings require more
        min_per_floor = 1 if floors >= 3 else max(1, required_beds - 1)
        if fbed < min_per_floor:
            missing.append(f"{label} floor Bedrooms (need at least {min_per_floor}, found {fbed})")
        if not any("bath" in t or "toilet" in t for t in frooms):
            missing.append(f"{label} floor Bathroom")

    return missing


def _place_rooms_dynamic(plot_w: float, plot_d: float, requirements: dict,
                         brahmasthan: dict, scale_factor: float = 1.0,
                         boundary_coords: list | None = None,
                         space_type: str = "residential",
                         preserved_walls: list[dict] | None = None,
                         constraint_filter: AdapterResult | None = None) -> list[dict]:
    """Place rooms on a plot using suite grouping for luxury villas.

    For 3+ floors or 5+ bedrooms on >= 50x50 ft plots:
      rooms are grouped into suites placed as units, then expanded.
    Otherwise: standard individual room placement.

    For commercial / mixed_use space_types the floor specification is sourced
    from the local commercial planning helper so that rooms like
    "Retail Floor", "Conference Room", "Office" are placed instead of
    residential rooms.

    This is a thin wrapper around compute_room_placements() that also
    normalizes the results via _normalize_room().  New code should call
    compute_room_placements() directly; plan_rooms() and
    create_canonical_spatial_plan() are the production entry points.
    """
    raw_placed = compute_room_placements(
        plot_w=plot_w, plot_d=plot_d, requirements=requirements,
        brahmasthan=brahmasthan, scale_factor=scale_factor,
        boundary_coords=boundary_coords, space_type=space_type,
        preserved_walls=preserved_walls,
        constraint_filter=constraint_filter,
    )
    # _normalize_room enriches each placement with computed fields
    # (canonical_id, area_sqft, zone, etc.).  The raw x/y/w/h from
    # compute_room_placements is authoritative placement data.
    normalized = [_normalize_room(room, index) for index, room in enumerate(raw_placed)]
    # Preserve constraint_ids_applied through normalization
    for norm, raw in zip(normalized, raw_placed):
        if "constraint_ids_applied" in raw:
            norm["constraint_ids_applied"] = raw["constraint_ids_applied"]
    return normalized



# ===========================================================================
# Shared placement algorithm — single source of truth for room x/y/w/h
# ===========================================================================

def compute_room_placements(
    plot_w: float, plot_d: float, requirements: dict,
    brahmasthan: dict, scale_factor: float = 1.0,
    boundary_coords: list | None = None,
    space_type: str = "residential",
    preserved_walls: list[dict] | None = None,
    constraint_filter: AdapterResult | None = None,
) -> list[dict]:
    """Shared room-placement algorithm used by both wrappers.

    This function contains the actual spatial placement logic — zone checks,
    preferred-position heuristics, collision detection, and scan-grid fallback.
    It is the **single source of truth** for room placement.

    Does NOT write to project_state, does NOT produce polygons, does NOT
    call exporters or migration.  Returns raw placement dicts with
    ``{id, type, floor, x, y, w, h, area_sqft}``.

    When *constraint_filter* is provided, PLANNER_SUPPORTED constraints are
    applied during placement: MIN_AREA/MAX_AREA set size bounds, ADJACENT_TO/
    NEAR/SEPARATED_FROM influence placement order, PREFERRED_ZONE changes
    placement priority, and CONTAINED_WITHIN enforces plot boundaries.  Each
    placed room records ``constraint_ids_applied`` trace metadata.
    """
    _polygon: list[tuple[float, float]] | None = None
    if boundary_coords and len(boundary_coords) > 4:
        _polygon = [(float(p[0]), float(p[1])) for p in boundary_coords]

    def _room_in_polygon(rx: float, ry: float, rw: float, rh: float) -> bool:
        if _polygon is None:
            return True
        def _pip(px, py, poly):
            inside = False
            n = len(poly)
            j = n - 1
            for i in range(n):
                xi, yi = poly[i]
                xj, yj = poly[j]
                if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
                    inside = not inside
                j = i
            return inside
        for cx, cy in [(rx, ry), (rx + rw, ry), (rx, ry + rh), (rx + rw, ry + rh)]:
            if not _pip(cx, cy, _polygon):
                return False
        return True

    bs_x = brahmasthan["x"]
    bs_y = brahmasthan["y"]
    bs_w = brahmasthan["w"]
    bs_h = brahmasthan["h"]

    # ── Constraint filter helpers ───────────────────────────────────────
    # Build lookup maps from constraint_filter for O(1) access during placement.
    _size_constraints: dict[str, dict] = {}   # room_type -> {min_area, max_area, min_width, max_width, ...}
    _adjacency_constraints: list[dict] = []   # adjacent_to / near / separated_from
    _zone_constraints: dict[str, str] = {}    # room_type -> preferred_zone
    _all_planner_specs: list[dict] = []       # raw planner spec dicts (for trace metadata)

    if constraint_filter is not None:
        _all_planner_specs = planner_constraints_as_room_specs(
            constraint_filter.planner_supported
        )
        for spec in _all_planner_specs:
            rt = spec.get("room_type", "")
            params = spec.get("parameters", {})
            # Size constraints
            has_size = any(k in params for k in (
                "min_area", "max_area", "min_width", "max_width", "min_depth", "max_depth",
            ))
            if has_size and rt:
                _size_constraints.setdefault(rt, {}).update({
                    k: v for k, v in params.items()
                    if k in ("min_area", "max_area", "min_width", "max_width", "min_depth", "max_depth")
                })
            # Adjacency constraints
            for adj_key in ("adjacent_to", "near", "separated_from"):
                if adj_key in params:
                    _adjacency_constraints.append({
                        "type": adj_key,
                        "room_type": rt,
                        "targets": params[adj_key] if isinstance(params[adj_key], list) else [params[adj_key]],
                        "constraint_id": spec.get("constraint_id"),
                        "rule_id": spec.get("rule_id"),
                        "parameters": params,
                    })
            # Zone preferences
            if "preferred_zone" in params and rt:
                _zone_constraints[rt] = str(params["preferred_zone"])

    def _size_for_room(room_type: str, base_rw: float, base_rh: float) -> tuple[float, float]:
        """Apply size constraints (MIN_AREA/MAX_AREA/MIN_WIDTH/MAX_WIDTH) to
        a room's dimensions. Returns (rw, rh) possibly adjusted.

        The function preserves area proportion unless the constraint forces
        a minimum that overrides the template.
        """
        sc = _size_constraints.get(room_type, {})
        if not sc:
            return base_rw, base_rh
        min_w = sc.get("min_width")
        max_w = sc.get("max_width")
        min_h = sc.get("min_depth", sc.get("min_height"))
        max_h = sc.get("max_depth", sc.get("max_height"))
        min_area = sc.get("min_area")
        max_area = sc.get("max_area")

        rw, rh = base_rw, base_rh
        # Apply width/depth bounds
        if min_w is not None:
            rw = max(rw, float(min_w))
        if max_w is not None:
            rw = min(rw, float(max_w))
        if min_h is not None:
            rh = max(rh, float(min_h))
        if max_h is not None:
            rh = min(rh, float(max_h))
        # Apply area bounds by adjusting the smaller dimension
        area = rw * rh
        if min_area is not None and area < float(min_area):
            # Scale up proportionally to meet minimum area
            scale = (float(min_area) / area) ** 0.5 if area > 0 else 1.0
            rw = round(rw * scale, 1)
            rh = round(rh * scale, 1)
        if max_area is not None and area > float(max_area):
            scale = (float(max_area) / area) ** 0.5
            rw = round(rw * scale, 1)
            rh = round(rh * scale, 1)
        return max(4.0, rw), max(4.0, rh)

    def _constraint_ids_for_room(room_type: str) -> list[str]:
        """Return constraint_ids that apply to a given room type."""
        ids: list[str] = []
        for spec in _all_planner_specs:
            if spec.get("room_type") == room_type:
                cid = spec.get("constraint_id")
                if cid:
                    ids.append(cid)
        return ids

    def _preferred_for_room_with_constraints(room_type: str, rw: float, rh: float) -> list:
        """Return preferred positions, enhanced by adjacency/zone constraints."""
        base_preferred = {
            "Master Bedroom": [(0.0, max(bs_y + bs_h, plot_d - rh))],
            "Master Suite": [(0.0, max(bs_y + bs_h, plot_d - rh))],
            "Bedroom 2": [(max(bs_x + bs_w, plot_w - rw), 0.0)],
            "Bedroom 3": [(max(bs_x + bs_w, plot_w - rw), 0.0)],
            "Bedroom 4": [(max(bs_x + bs_w, plot_w - rw), 0.0)],
            "Bedroom 5": [(max(bs_x + bs_w, plot_w - rw), 0.0)],
            "Bedroom 6": [(max(bs_x + bs_w, plot_w - rw), 0.0)],
            "Guest Bedroom": [(max(bs_x + bs_w, plot_w - rw), 0.0)],
            "Living Room": [(0.0, 0.0)],
            "Formal Living": [(0.0, 0.0)],
            "Drawing Room": [(0.0, 0.0)],
            "Family Lounge": [(max(bs_x + bs_w, plot_w - rw), 0.0)],
            "Dining Room": [(max(bs_x + bs_w, plot_w - rw), max(bs_y + bs_h, plot_d - rh))],
            "Kitchen": [(max(bs_x + bs_w, plot_w - rw), max(bs_y + bs_h, plot_d - rh)),
                         (plot_w - rw, max(bs_y + bs_h, plot_d - rh))],
            "Pooja Room": [(max(bs_x + bs_w, plot_w - rw), 0.0)],
            "Bathroom": [(0.0, max(bs_y + bs_h, plot_d - rh))],
            "Attached Bathroom": [(0.0, max(bs_y + bs_h, plot_d - rh))],
            "Common Bathroom": [(0.0, max(bs_y + bs_h, plot_d - rh))],
            "Staircase": [(max(bs_x + bs_w, plot_w - rw), 0.0)],
            "Verandah": [(0.0, max(bs_y + bs_h, plot_d - rh))],
            "Foyer": [(0.0, 0.0)],
            "Home Theater": [(0.0, 0.0)],
            "Gym": [(max(bs_x + bs_w, plot_w - rw), 0.0)],
            "Store": [(max(bs_x + bs_w, plot_w - rw), max(bs_y + bs_h, plot_d - rh))],
            "Utility": [(plot_w - rw, max(bs_y + bs_h, plot_d - rh))],
            "Laundry": [(plot_w - rw, plot_d - rh)],
            "Covered Terrace": [(0.0, max(bs_y + bs_h, plot_d - rh))],
            "Open Terrace": [(0.0, 0.0)],
            "Powder Room": [(0.0, max(bs_y + bs_h, plot_d - rh))],
            "Walk-in Closet": [(0.0, max(bs_y + bs_h, plot_d - rh))],
            "Dressing Room": [(0.0, max(bs_y + bs_h, plot_d - rh))],
            "Servant's Room": [(max(bs_x + bs_w, plot_w - rw), max(bs_y + bs_h, plot_d - rh))],
            "Servant Room": [(max(bs_x + bs_w, plot_w - rw), max(bs_y + bs_h, plot_d - rh))],
            "Balcony": [(max(bs_x + bs_w, plot_w - rw), max(bs_y + bs_h, plot_d - rh))],
            "Double Garage": [(plot_w - rw, plot_d - rh)],
            "Multi-car Garage": [(plot_w - rw, plot_d - rh)],
        }.get(room_type, [(0.0, 0.0)])

        # If a zone preference constraint exists, bias positions toward that zone
        preferred_zone = _zone_constraints.get(room_type)
        if preferred_zone and preferred_zone != "Any zone":
            zone_positions = {
                "NE": [(plot_w - rw, plot_d - rh)],
                "N":  [(plot_w / 2 - rw / 2, plot_d - rh)],
                "NW": [(0.0, plot_d - rh)],
                "E":  [(plot_w - rw, plot_d / 2 - rh / 2)],
                "C":  [(plot_w / 2 - rw / 2, plot_d / 2 - rh / 2)],
                "W":  [(0.0, plot_d / 2 - rh / 2)],
                "SE": [(plot_w - rw, 0.0)],
                "S":  [(plot_w / 2 - rw / 2, 0.0)],
                "SW": [(0.0, 0.0)],
            }
            zone_pos = zone_positions.get(preferred_zone, [])
            if zone_pos:
                # Prepend zone-biased positions ahead of defaults
                base_preferred = zone_pos + base_preferred

        # Apply adjacency constraints: if adjacent_to targets are already placed,
        # try positions near those targets first.
        if _adjacency_constraints:
            for adj in _adjacency_constraints:
                if adj.get("room_type") != room_type:
                    continue
                targets = adj.get("targets", [])
                if not targets:
                    continue
                adj_positions = []
                for p in placed:
                    if p.get("type") in targets and p.get("floor") == rfloor:
                        px, py = p["x"], p["y"]
                        pw, ph = p["w"], p["h"]
                        # Positions adjacent to the target
                        adj_positions.extend([
                            (px + pw + 0.1, py),          # right
                            (px - rw - 0.1, py),           # left
                            (px, py + ph + 0.1),            # below
                            (px, py - rh - 0.1),            # above
                        ])
                if adj_positions:
                    # Filter valid positions and prepend
                    valid_adj = [
                        (round(x, 1), round(y, 1))
                        for x, y in adj_positions
                        if x >= -0.1 and y >= -0.1 and x + rw <= plot_w + 0.1 and y + rh <= plot_d + 0.1
                    ]
                    base_preferred = valid_adj + base_preferred
                    break  # only apply first matching adjacency rule

        return base_preferred

    def overlaps_bs(x, y, w, h):
        return x < bs_x + bs_w and x + w > bs_x and y < bs_y + bs_h and y + h > bs_y

    cell_w = plot_w / 3.0
    cell_d = plot_d / 3.0

    forbidden_zones = {
        "Kitchen": {"NE", "N", "C"},
        "Bathroom": {"NE", "C"},
        "Ground Floor Bathroom": {"NE", "C"},
        "Attached Bathroom": {"NE", "C"},
        "Common Bathroom": {"NE", "C"},
        "Master Bathroom": {"NE", "C"},
        "Toilet": {"NE", "C"},
        "Powder Room": {"NE", "C"},
        "Staircase": {"NE", "C"},
        "Laundry": {"NE", "C"},
        "Master Suite": {"NE", "N", "C"},
        "Guest Suite": {"NE", "N", "C"},
    }

    def _check_forbidden(x, y, w, h, primary_type):
        fz = forbidden_zones.get(primary_type, set())
        if not fz:
            return True
        cx, cy = x + w / 2.0, y + h / 2.0
        col = min(2, int(cx / cell_w)) if cell_w > 0 else 1
        row = min(2, int(cy / cell_d)) if cell_d > 0 else 1
        return [["NW", "N", "NE"], ["W", "C", "E"], ["SW", "S", "SE"]][row][col] not in fz

    floors = int(requirements.get("floors", 1))
    bedrooms = int(requirements.get("bedrooms", 2))
    bathrooms = int(requirements.get("bathrooms", 1))
    is_villa = floors >= 3 or bedrooms >= 5

    placed: list[dict] = []

    if is_villa and plot_w >= 50.0 and plot_d >= 50.0:
        specs = _build_villa_floor_specs(plot_w, plot_d, requirements)
        for rfloor in sorted(specs.keys()):
            for room_type, base_rw, base_rh in specs[rfloor]:
                rw = round(base_rw * scale_factor, 1)
                rh = round(base_rh * scale_factor, 1)
                pos = None
                preferred = {
                    "Master Suite":     [(0.0, max(bs_y + bs_h, plot_d - rh))],
                    "Master Bedroom":   [(0.0, max(bs_y + bs_h, plot_d - rh))],
                    "Bedroom 2":        [(max(bs_x + bs_w, plot_w - rw), 0.0)],
                    "Bedroom 3":        [(max(bs_x + bs_w, plot_w - rw), 0.0)],
                    "Bedroom 4":        [(max(bs_x + bs_w, plot_w - rw), 0.0)],
                    "Bedroom 5":        [(max(bs_x + bs_w, plot_w - rw), 0.0)],
                    "Bedroom 6":        [(max(bs_x + bs_w, plot_w - rw), 0.0)],
                    "Guest Bedroom":    [(max(bs_x + bs_w, plot_w - rw), 0.0)],
                    "Living Room":      [(0.0, 0.0)],
                    "Formal Living":    [(0.0, 0.0)],
                    "Drawing Room":     [(0.0, 0.0)],
                    "Family Lounge":    [(max(bs_x + bs_w, plot_w - rw), 0.0)],
                    "Dining Room":      [(max(bs_x + bs_w, plot_w - rw), max(bs_y + bs_h, plot_d - rh))],
                    "Kitchen":          [(max(bs_x + bs_w, plot_w - rw), max(bs_y + bs_h, plot_d - rh)),
                                         (plot_w - rw, max(bs_y + bs_h, plot_d - rh))],
                    "Pooja Room":       [(max(bs_x + bs_w, plot_w - rw), 0.0)],
                    "Bathroom":         [(0.0, max(bs_y + bs_h, plot_d - rh))],
                    "Attached Bathroom": [(0.0, max(bs_y + bs_h, plot_d - rh))],
                    "Common Bathroom":   [(0.0, max(bs_y + bs_h, plot_d - rh))],
                    "Staircase":        [(max(bs_x + bs_w, plot_w - rw), 0.0)],
                    "Verandah":         [(0.0, max(bs_y + bs_h, plot_d - rh))],
                    "Foyer":            [(0.0, 0.0)],
                    "Home Theater":     [(0.0, 0.0)],
                    "Gym":              [(max(bs_x + bs_w, plot_w - rw), 0.0)],
                    "Store":            [(max(bs_x + bs_w, plot_w - rw), max(bs_y + bs_h, plot_d - rh))],
                    "Utility":          [(plot_w - rw, max(bs_y + bs_h, plot_d - rh))],
                    "Laundry":          [(plot_w - rw, plot_d - rh)],
                    "Covered Terrace":  [(0.0, max(bs_y + bs_h, plot_d - rh))],
                    "Open Terrace":     [(0.0, 0.0)],
                    "Powder Room":      [(0.0, max(bs_y + bs_h, plot_d - rh))],
                    "Walk-in Closet":   [(0.0, max(bs_y + bs_h, plot_d - rh))],
                    "Dressing Room":    [(0.0, max(bs_y + bs_h, plot_d - rh))],
                    "Servant's Room":  [(max(bs_x + bs_w, plot_w - rw), max(bs_y + bs_h, plot_d - rh))],
                    "Servant Room":     [(max(bs_x + bs_w, plot_w - rw), max(bs_y + bs_h, plot_d - rh))],
                    "Balcony":          [(max(bs_x + bs_w, plot_w - rw), max(bs_y + bs_h, plot_d - rh))],
                    "Double Garage":    [(plot_w - rw, plot_d - rh)],
                    "Multi-car Garage": [(plot_w - rw, plot_d - rh)],
                }.get(room_type, [(0.0, 0.0)])

                # Apply constraint-driven size overrides
                if constraint_filter is not None:
                    rw, rh = _size_for_room(room_type, rw, rh)

                def can_place_villa(x, y):
                    if x < 0 or y < 0 or x + rw > plot_w or y + rh > plot_d:
                        return False
                    if not _room_in_polygon(x, y, rw, rh):
                        return False
                    if overlaps_bs(x, y, rw, rh):
                        return False
                    for p in placed:
                        if p["floor"] == rfloor:
                            if (x < p["x"] + p["w"] and x + rw > p["x"] and
                                y < p["y"] + p["h"] and y + rh > p["y"]):
                                return False
                            if _clearance_between(
                                {"x": x, "y": y, "w": rw, "h": rh}, p,
                            ) < _MIN_CLEARANCE:
                                return False
                    fz = forbidden_zones.get(room_type, set())
                    if fz:
                        cx, cy = x + rw / 2.0, y + rh / 2.0
                        col = min(2, int(cx / (plot_w / 3.0))) if plot_w > 0 else 1
                        row = min(2, int(cy / (plot_d / 3.0))) if plot_d > 0 else 1
                        zone = [["NW", "N", "NE"], ["W", "C", "E"], ["SW", "S", "SE"]][row][col]
                        if zone in fz:
                            return False
                    return True

                preferred = _preferred_for_room_with_constraints(room_type, rw, rh)

                for x_try, y_try in preferred:
                    x_try, y_try = round(x_try, 1), round(y_try, 1)
                    if can_place_villa(x_try, y_try):
                        pos = (x_try, y_try)
                        break

                if not pos:
                    for scan_y in range(0, max(0, int(plot_d - rh)) + 1):
                        for scan_x in range(0, max(0, int(plot_w - rw)) + 1):
                            if can_place_villa(scan_x, scan_y):
                                pos = (float(scan_x), float(scan_y))
                                break
                        if pos:
                            break

                if pos:
                    placed.append({
                        "id": f"R{len(placed) + 1}",
                        "type": room_type,
                        "floor": rfloor,
                        "x": pos[0], "y": pos[1],
                        "w": rw, "h": rh,
                        "area_sqft": round(rw * rh, 2),
                        "constraint_ids_applied": _constraint_ids_for_room(room_type),
                    })

        return placed

    if is_commercial_space_type(space_type):
        floor_specs: dict[int, list] = {0: []}
        for (room_type, base_rw, base_rh) in build_commercial_floor_spec(space_type):
            floor_specs[0].append((room_type, float(base_rw), float(base_rh)))
    else:
        floor_specs: dict[int, list] = {
            0: [("Living Room", 12.0, 14.0), ("Kitchen", 8.0, 10.0)],
        }
        if bathrooms >= 1:
            floor_specs[0].append(("Ground Floor Bathroom", 5.0, 8.0))
        if floors >= 2:
            floor_specs[0].append(("Staircase", 5.0, 8.0))

        kitchens = int(requirements.get("kitchens", 1))
        for k_idx in range(1, kitchens):
            label = f"Kitchen {k_idx + 1}"
            size = (7.0, 9.0) if k_idx == 1 else (6.0, 8.0)
            floor_specs.setdefault(0, []).append((label, size[0], size[1]))

        entrances = requirements.get("entrances", []) or []
        if "main" in entrances:
            floor_specs[0].append(("Foyer", 6.0, 8.0))
        if "service" in entrances:
            floor_specs[0].append(("Service Room", 5.0, 8.0))

        total_upper_floors = max(1, floors - 1)
        beds_per_upper = bedrooms // total_upper_floors
        beds_remainder = bedrooms % total_upper_floors

        for fl in range(1, floors):
            floor_specs[fl] = []
            beds_this_floor = beds_per_upper + (1 if fl <= beds_remainder else 0)
            if fl == 1:
                floor_specs[fl].append(("Master Bedroom", 12.0, 12.0))
                beds_this_floor -= 1
            for i in range(max(0, beds_this_floor)):
                label = f"Bedroom {i + 2}"
                floor_specs[fl].append((label, 10.0, 10.0))
            for _ in range(max(1, (bathrooms // total_upper_floors) + (1 if fl <= (bathrooms % total_upper_floors) else 0))):
                floor_specs[fl].append(("Bathroom", 5.0, 8.0))
            if floors >= 2:
                floor_specs[fl].append(("Staircase", 5.0, 8.0))

        if floors == 1:
            if bedrooms >= 1:
                floor_specs[0].append(("Master Bedroom", 12.0, 12.0))
            for i in range(max(0, bedrooms - 1)):
                label = f"Bedroom {i + 2}"
                floor_specs[0].append((label, 10.0, 10.0))

    for rfloor in sorted(floor_specs.keys()):
        for room_type, base_rw, base_rh in floor_specs[rfloor]:
            rw = round(base_rw * scale_factor, 1)
            rh = round(base_rh * scale_factor, 1)
            pos = None

            # Apply constraint-driven size overrides before building size_attempts
            if constraint_filter is not None:
                rw, rh = _size_for_room(room_type, rw, rh)

            size_attempts = [(rw, rh)]
            if rw != rh:
                size_attempts.append((rh, rw))
            if room_type not in ("Staircase",):
                for shrink in (0.9, 0.8, 0.7, 0.6):
                    size_attempts.append((round(rw * shrink, 1), round(rh * shrink, 1)))

            for rw, rh in size_attempts:
                if rw <= 0 or rh <= 0:
                    continue
                preferred = _preferred_for_room_with_constraints(room_type, rw, rh)

                def _can_place(x, y, rw=rw, rh=rh):
                    if x < 0 or y < 0 or x + rw > plot_w + 0.01 or y + rh > plot_d + 0.01:
                        return False
                    if not _room_in_polygon(x, y, rw, rh):
                        return False
                    if overlaps_bs(x, y, rw, rh):
                        return False
                    if preserved_walls:
                        for wall in preserved_walls:
                            if _wall_blocks_room(x, y, rw, rh, wall):
                                return False
                    for p in placed:
                        if p["floor"] == rfloor:
                            if (x < p["x"] + p["w"] and x + rw > p["x"] and
                                y < p["y"] + p["h"] and y + rh > p["y"]):
                                return False
                            if _clearance_between(
                                {"x": x, "y": y, "w": rw, "h": rh}, p,
                            ) < _MIN_CLEARANCE:
                                return False
                    return _check_forbidden(x, y, rw, rh, room_type)

                for x_try, y_try in preferred:
                    x_try, y_try = round(x_try, 1), round(y_try, 1)
                    if _can_place(x_try, y_try):
                        pos = (x_try, y_try)
                        break

                if not pos and is_commercial_space_type(space_type):
                    _neighbors = COMMERCIAL_ADJACENCY_RULES.get(room_type, [])
                    if _neighbors:
                        _adj_candidates = []
                        for p in placed:
                            if p["floor"] != rfloor or p["type"] not in _neighbors:
                                continue
                            for dx, dy in [
                                (-rw - _MIN_CLEARANCE, 0.0),
                                (p["w"] + _MIN_CLEARANCE, 0.0),
                                (0.0, -rh - _MIN_CLEARANCE),
                                (0.0, p["h"] + _MIN_CLEARANCE),
                            ]:
                                cx = round(p["x"] + dx, 1)
                                cy = round(p["y"] + dy, 1)
                                if cx >= 0 and cy >= 0:
                                    _adj_candidates.append((cx, cy))
                        for x_try, y_try in _adj_candidates:
                            if _can_place(x_try, y_try):
                                pos = (x_try, y_try)
                                break

                if not pos:
                    for scan_y in range(0, max(0, int(plot_d - rh)) + 1):
                        for scan_x in range(0, max(0, int(plot_w - rw)) + 1):
                            if _can_place(scan_x, scan_y):
                                pos = (float(scan_x), float(scan_y))
                                break
                        if pos:
                            break

                if pos:
                    break

            if pos:
                placed.append({
                    "id": f"R{len(placed) + 1}",
                    "type": room_type,
                    "floor": rfloor,
                    "x": pos[0], "y": pos[1],
                    "w": rw, "h": rh,
                    "area_sqft": round(rw * rh, 2),
                    "constraint_ids_applied": _constraint_ids_for_room(room_type),
                })

    return placed



def _requires_window(room_type: str) -> bool:
    """Return True if room_type is a habitable room that needs at least one window."""
    normalized = room_type.strip().title()
    habitable = {
        "Living Room", "Master Bedroom", "Bedroom", "Bedroom 2", "Bedroom 3",
        "Bedroom 4", "Bedroom 5", "Bedroom 6", "Kitchen", "Family Lounge",
        "Drawing Room", "Dining Room", "Guest Bedroom", "Study", "Pooja Room",
        "Formal Living", "Home Theater", "Gym", "Servant's Room", "Servant Room",
    }
    return normalized in habitable


def _exterior_walls(room: dict, plot_w: float, plot_d: float) -> set[str]:
    """Return set of wall directions (N/S/E/W) that touch the plot boundary."""
    walls: set[str] = set()
    x = _as_float(room.get("x"))
    y = _as_float(room.get("y"))
    w = _as_float(room.get("w"))
    h = _as_float(room.get("h"))
    if y <= _EPSILON:
        walls.add("N")
    if y + h >= plot_d - _EPSILON:
        walls.add("S")
    if x <= _EPSILON:
        walls.add("W")
    if x + w >= plot_w - _EPSILON:
        walls.add("E")
    return walls


def _windows_for_room(room: dict, plot_w: float, plot_d: float) -> list[dict]:
    """Return window placement list for a single room."""
    ext_walls = _exterior_walls(room, plot_w, plot_d)
    windows: list[dict] = []
    for wall in sorted(ext_walls):
        if wall == "E":
            offset = float(room.get("h", 0)) / 2.0
        elif wall == "W":
            offset = float(room.get("h", 0)) / 2.0
        elif wall == "N":
            offset = float(room.get("w", 0)) / 2.0
        elif wall == "S":
            offset = float(room.get("w", 0)) / 2.0
        windows.append({
            "wall": wall,
            "offset_ft": round(offset, 1),
            "width_ft": 3.0,
        })
    return windows


def _attach_ventilation(rooms: list[dict], plot_w: float, plot_d: float) -> list[dict]:
    """Post-placement step: attach windows metadata to each room."""
    for room in rooms:
        rtype = str(room.get("type", ""))
        windows = _windows_for_room(room, plot_w, plot_d)
        room["windows"] = windows
        room["ventilation_met"] = (
            _requires_window(rtype) and len(windows) >= 1
        )
    return rooms

# ---- Door helpers ----

def _shared_wall(room_a: dict, room_b: dict) -> str | None:
    """Return the wall direction (N/S/E/W) where two rooms touch, or None."""
    ax1, ay1, ax2, ay2 = _rect(room_a)
    bx1, by1, bx2, by2 = _rect(room_b)
    # Horizontal adjacency
    if abs(ax2 - bx1) < _EPSILON and ay1 < by2 - _EPSILON and ay2 > by1 + _EPSILON:
        return "E"
    if abs(bx2 - ax1) < _EPSILON and ay1 < by2 - _EPSILON and ay2 > by1 + _EPSILON:
        return "W"
    # Vertical adjacency
    if abs(ay2 - by1) < _EPSILON and ax1 < bx2 - _EPSILON and ax2 > bx1 + _EPSILON:
        return "S"
    if abs(by2 - ay1) < _EPSILON and ax1 < bx2 - _EPSILON and ax2 > bx1 + _EPSILON:
        return "N"
    return None


def _door_for_room(room: dict, adjacent: dict | None = None,
                   plot_w: float = 36.0, plot_d: float = 32.0) -> dict | None:
    """Return a door dict for a room, or None if no door can be placed.

    If an adjacent room is provided and they share a wall, place the door on
    that shared wall.  If adjacent is provided but they don't share a wall,
    return None.  If no adjacent is provided, place an entry door on an
    exterior wall.
    """
    # Priority 1: internal door on shared wall with adjacent room
    if adjacent is not None:
        wall = _shared_wall(room, adjacent)
        if wall:
            if wall in ("E", "W"):
                offset = float(room.get("h", 0)) / 2.0
            else:
                offset = float(room.get("w", 0)) / 2.0
            return {"wall": wall, "offset_ft": round(offset, 1), "swings_into": room["id"]}
        return None  # adjacent given but no shared wall

    # Priority 2: entry door on exterior wall (no adjacent provided)
    ext_walls = _exterior_walls(room, plot_w, plot_d)
    if ext_walls:
        wall = sorted(ext_walls)[0]
        if wall in ("E", "W"):
            offset = float(room.get("h", 0)) / 2.0
        else:
            offset = float(room.get("w", 0)) / 2.0
        return {"wall": wall, "offset_ft": round(offset, 1), "swings_into": room["id"]}

    return None


def _attach_doors(rooms: list[dict]) -> list[dict]:
    """Post-placement step: attach door metadata to each room."""
    result = list(rooms)
    paired: set = set()

    # First pass: doors between adjacent rooms on the same floor
    for i, a in enumerate(result):
        for j, b in enumerate(result):
            if j <= i or i in paired or j in paired:
                continue
            if a.get("floor") != b.get("floor"):
                continue
            wall = _shared_wall(a, b)
            if wall:
                offset_a = float(a.get("h" if wall in ("E", "W") else "w", 0)) / 2.0
                offset_b = float(b.get("h" if wall in ("E", "W") else "w", 0)) / 2.0
                b_wall = {"E": "W", "W": "E", "N": "S", "S": "N"}[wall]
                result[i]["door"] = {"wall": wall, "offset_ft": round(offset_a, 1), "swings_into": a["id"]}
                result[j]["door"] = {"wall": b_wall, "offset_ft": round(offset_b, 1), "swings_into": b["id"]}
                paired.add(i)
                paired.add(j)

    # Second pass: entry doors for rooms without one
    for idx, room in enumerate(result):
        if "door" in room:
            continue
        door = _door_for_room(room)
        if door:
            result[idx]["door"] = door

    return result


def _check_circulation(rooms: list[dict]) -> list[str]:
    """Return list of rooms missing a door."""
    issues: list[str] = []
    for room in rooms:
        if "door" not in room:
            rtype = room.get("type", "?")
            rid = room.get("id", "?")
            issues.append(f"Room {rid} ({rtype}) has no door — circulation blocked.")
    return issues


def _entry_path(target: dict, all_rooms: list[dict]) -> dict:
    """Return the shortest path from the entry room to the target room.

    Entry room is the first Living Room / Foyer / Formal Living in the list.
    Returns {"distance_ft": float, "via": str}.
    """
    if not all_rooms:
        return {"distance_ft": float("inf"), "via": "?"}

    # Find entry room
    entry = None
    for r in all_rooms:
        if r.get("type") in ("Living Room", "Foyer", "Formal Living"):
            entry = r
            break
    if entry is None:
        entry = all_rooms[0]

    # Target IS the entry → zero distance
    if entry.get("id") == target.get("id"):
        return {"distance_ft": 0.0, "via": entry.get("id", "?")}

    # Manhattan distance from entry center to target center
    dist = abs(target["x"] - entry["x"]) + abs(target["y"] - entry["y"])
    return {"distance_ft": round(dist, 1), "via": entry.get("id", "?")}


def _apply_suite_metadata(rooms: list[dict]) -> list[dict]:
    """Annotate placed rooms with suite_group (from SUITE_MEMBERSHIP) and
    staircase_id (links multi-floor staircase rooms into one continuous stair).

    This is a post-placement step: rooms are already placed; we only attach
    metadata for downstream consumers (compliance report, SVG renderer, DXF
    export, etc.) so the Master Suite and Family Block become recognizable
    groups rather than disconnected rectangles.
    """
    staircase_counter = 0
    last_staircase_id: str | None = None
    last_staircase_position: tuple[float, float] | None = None
    for room in rooms:
        rtype = str(room.get("type") or "")
        suite = SUITE_MEMBERSHIP.get(rtype)
        if suite:
            room["suite_group"] = suite
        if rtype == "Staircase":
            x = float(room.get("x", 0))
            y = float(room.get("y", 0))
            if last_staircase_position is not None:
                dx = abs(x - last_staircase_position[0])
                dy = abs(y - last_staircase_position[1])
                # Stairs on different floors can share the same x,y plan position
                # even if one floor has the stair at y=0 and another at y=offset.
                # Match when centers align within one staircase width/height.
                if dx <= 8.0 and dy <= 8.0:
                    room["staircase_id"] = last_staircase_id
                else:
                    staircase_counter += 1
                    last_staircase_id = f"STAIR_{staircase_counter:02d}"
                    room["staircase_id"] = last_staircase_id
                    last_staircase_position = (x, y)
            else:
                staircase_counter += 1
                last_staircase_id = f"STAIR_{staircase_counter:02d}"
                room["staircase_id"] = last_staircase_id
                last_staircase_position = (x, y)

    # Build staircase_id → set of floor numbers served, then attach the sorted
    # list back onto each staircase room so DXF/compliance/SVG can verify
    # continuous multi-floor circulation.
    floors_by_stair: dict[str, set[int]] = {}
    for room in rooms:
        sid = room.get("staircase_id")
        if not sid:
            continue
        floors_by_stair.setdefault(sid, set()).add(int(room.get("floor", 0)))
    for room in rooms:
        sid = room.get("staircase_id")
        if not sid:
            continue
        room["staircase_floors_served"] = sorted(floors_by_stair[sid])
    return rooms


# ── Non-rectangular room helpers (L-shaped, T-shaped) ──────────────


def _l_shaped_vertices(
    x: float, y: float, w: float, h: float,
    cut_w: float, cut_h: float, corner: str,
) -> list[tuple[float, float]]:
    """Return 6 vertices for an L-shaped room carved from a rectangle.

    The 'corner' argument selects which corner of the rectangle is removed:
      NE, NW, SE, SW. cut_w and cut_h are the width and height of the
    removed notch. If either is 0, returns a 4-vertex rectangle.
    """
    if cut_w <= 0 or cut_h <= 0:
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]

    x1, y1 = x, y
    x2, y2 = x + w, y + h

    if corner == "NE":
        return [(x1, y1), (x2 - cut_w, y1), (x2 - cut_w, y2 - cut_h),
                (x2, y2 - cut_h), (x2, y2), (x1, y2)]
    if corner == "NW":
        return [(x1, y1), (x2, y1), (x2, y2), (x1 + cut_w, y2),
                (x1 + cut_w, y1 + cut_h), (x1, y1 + cut_h)]
    if corner == "SE":
        return [(x1, y1), (x2, y1), (x2, y1 + cut_h), (x1 + cut_w, y1 + cut_h),
                (x1 + cut_w, y2), (x1, y2)]
    if corner == "SW":
        return [(x1 + cut_w, y1), (x2, y1), (x2, y2), (x1, y2),
                (x1, y1 + cut_h), (x1 + cut_w, y1 + cut_h)]
    # Default: full rectangle
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def _t_shaped_vertices(
    x: float, y: float, w: float, h: float,
    bar_w: float, bar_h: float,
) -> list[tuple[float, float]]:
    """Return 8 vertices for a T-shaped room.

    The 'bar' is a horizontal strip at the top spanning the full width.
    The 'stem' descends from the center of the bar.
    bar_w < w: stem is narrower than the bar.
    """
    if bar_w <= 0 or bar_h <= 0 or bar_h >= h or bar_w >= w:
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]

    cx = x + w / 2.0
    half = bar_w / 2.0
    stem_left = cx - half
    stem_right = cx + half
    bar_bot = y + bar_h
    return [
        (x, y),
        (x + w, y),
        (x + w, bar_bot),
        (stem_right, bar_bot),
        (stem_right, y + h),
        (stem_left, y + h),
        (stem_left, bar_bot),
        (x, bar_bot),
    ]


# ── Renovation helpers (preserved walls, load-bearing checks) ─────


def _wall_blocks_room(rx: float, ry: float, rw: float, rh: float,
                      wall: dict, epsilon: float = 0.5) -> bool:
    """Return True if a room would overlap a preserved wall.

    A room 'blocks' a wall when its interior crosses the wall line.
    Rooms that merely share a boundary (touch at epsilon) are allowed.
    """
    if not wall:
        return False
    wx1, wy1 = float(wall.get("start", (0, 0))[0]), float(wall.get("start", (0, 0))[1])
    wx2, wy2 = float(wall.get("end", (0, 0))[0]),   float(wall.get("end", (0, 0))[1])

    # Horizontal wall
    if abs(wy1 - wy2) < epsilon:
        wy = (wy1 + wy2) / 2.0
        room_top = ry
        room_bot = ry + rh
        return room_top < wy - epsilon and room_bot > wy + epsilon

    # Vertical wall
    if abs(wx1 - wx2) < epsilon:
        wx = (wx1 + wx2) / 2.0
        room_left = rx
        room_right = rx + rw
        return room_left < wx - epsilon and room_right > wx + epsilon

    return False


def _check_preserved_wall_overlaps(rooms: list[dict],
                                   preserved_walls: list[dict] | None) -> list[str]:
    """Return error strings for any room that overlaps a preserved wall."""
    if not preserved_walls:
        return []
    errors: list[str] = []
    for room in rooms:
        rx, ry, rw, rh = room.get("x", 0), room.get("y", 0), room.get("w", 0), room.get("h", 0)
        for wall in preserved_walls:
            if _wall_blocks_room(rx, ry, rw, rh, wall):
                errors.append(
                    f"Room {room.get('id', '?')} overlaps preserved wall {wall.get('id', '?')}"
                )
    return errors


def _door_on_load_bearing_wall(room: dict, lb_walls: list[dict]) -> bool:
    """Return True if the room's door sits on a load-bearing wall.

    Only walls with type 'load_bearing' are checked; party_wall and other
    types are ignored.
    """
    door = room.get("door")
    if not door:
        return False
    door_wall = str(door.get("wall", "")).upper()
    door_offset = float(door.get("offset_ft", 0))
    rx, ry, rw, rh = room.get("x", 0), room.get("y", 0), room.get("w", 0), room.get("h", 0)

    for wall in lb_walls:
        if str(wall.get("type", "")).lower() != "load_bearing":
            continue
        wx1, wy1 = float(wall.get("start", (0, 0))[0]), float(wall.get("start", (0, 0))[1])
        wx2, wy2 = float(wall.get("end", (0, 0))[0]),   float(wall.get("end", (0, 0))[1])

        # Horizontal load-bearing wall
        if abs(wy1 - wy2) < 0.5:
            wy = (wy1 + wy2) / 2.0
            if door_wall == "S" and abs(ry + rh - wy) < 1.0:
                if wx1 - 0.5 <= rx + door_offset <= wx2 + 0.5:
                    return True
            if door_wall == "N" and abs(ry - wy) < 1.0:
                if wx1 - 0.5 <= rx + door_offset <= wx2 + 0.5:
                    return True

        # Vertical load-bearing wall
        if abs(wx1 - wx2) < 0.5:
            wx = (wx1 + wx2) / 2.0
            if door_wall == "E" and abs(rx + rw - wx) < 1.0:
                if wy1 - 0.5 <= ry + door_offset <= wy2 + 0.5:
                    return True
            if door_wall == "W" and abs(rx - wx) < 1.0:
                if wy1 - 0.5 <= ry + door_offset <= wy2 + 0.5:
                    return True

    return False


def _check_load_bearing_doors(rooms: list[dict],
                              lb_walls: list[dict] | None) -> list[str]:
    """Return error strings for doors placed on load-bearing walls."""
    if not lb_walls:
        return []
    errors: list[str] = []
    for room in rooms:
        if _door_on_load_bearing_wall(room, lb_walls):
            errors.append(
                f"Room {room.get('id', '?')} has a door on a load-bearing wall"
            )
    return errors


def _fallback_required_rooms(project_state: dict,
                              constraint_filter: AdapterResult | None = None) -> list[dict]:
    plot_w, plot_d = _plot_dimensions(project_state)
    brahmasthan = _brahmasthan_bounds(plot_w, plot_d)
    space_type = project_state.get("space_type", "residential")
    boundary_coords = project_state.get("plot", {}).get("boundary_coords")
    rooms = _place_rooms_dynamic(
        plot_w, plot_d,
        project_state.get("requirements", {}),
        brahmasthan,
        boundary_coords=boundary_coords,
        space_type=space_type,
        constraint_filter=constraint_filter,
    )
    rooms = _attach_ventilation(rooms, plot_w, plot_d)
    rooms = _attach_doors(rooms)
    return _apply_suite_metadata(rooms)


def _mock_llm_rooms(project_state: dict,
                    constraint_filter: AdapterResult | None = None) -> list[dict]:
    plot_w, plot_d = _plot_dimensions(project_state)
    brahmasthan = _brahmasthan_bounds(plot_w, plot_d)
    scale_factor = _recommended_scale(project_state)
    space_type = project_state.get("space_type", "residential")
    rooms = _place_rooms_dynamic(
        plot_w, plot_d,
        project_state.get("requirements", {}),
        brahmasthan,
        scale_factor=scale_factor,
        boundary_coords=project_state.get("plot", {}).get("boundary_coords"),
        space_type=space_type,
        constraint_filter=constraint_filter,
    )
    rooms = _attach_ventilation(rooms, plot_w, plot_d)
    rooms = _attach_doors(rooms)
    return _apply_suite_metadata(rooms)


def _get_rooms_from_gemini(project_state: dict, brahmasthan: dict,
                           constraint_filter: AdapterResult | None = None) -> list[dict]:
    last_error: str | None = None
    for attempt in range(4):
        prompt = _build_prompt(project_state, brahmasthan, previous_error=last_error)
        response_text = _call_llm(prompt)
        try:
            parsed = _extract_json_array(response_text)
            rooms = [_normalize_room(room, index) for index, room in enumerate(parsed)]
            plot_w, plot_d = _plot_dimensions(project_state)
            validation_errors = _validate_rooms(rooms, plot_w, plot_d, brahmasthan)
            if validation_errors:
                raise ValueError("; ".join(validation_errors))
            missing_rooms = _check_required_rooms(rooms, project_state.get("requirements", {}))
            if missing_rooms:
                raise ValueError(f"Required rooms missing: {', '.join(missing_rooms)}")
            return rooms
        except Exception as exc:
            last_error = str(exc)
            if attempt == 3:
                fallback_rooms = _fallback_required_rooms(project_state, constraint_filter=constraint_filter)
                plot_w, plot_d = _plot_dimensions(project_state)
                validation_errors = _validate_rooms(fallback_rooms, plot_w, plot_d, brahmasthan)
                missing_rooms = _check_required_rooms(fallback_rooms, project_state.get("requirements", {}))
                if validation_errors or missing_rooms:
                    details = "; ".join(validation_errors + [f"Required rooms missing: {', '.join(missing_rooms)}"] if missing_rooms else validation_errors)
                    raise ValueError(f"Gemini spatial plan failed validation after retry: {last_error}; fallback invalid: {details}") from exc
                return fallback_rooms
    raise ValueError("Gemini spatial plan failed unexpectedly.")


def _add_checkpoint(project_state: dict, summary: str) -> None:
    project_state.setdefault("checkpoint_history", []).append(
        {
            "version": project_state["version"],
            "agent": "SpatialPlanner",
            "timestamp": _now_iso(),
            "summary": summary,
            "rooms_count": len(project_state.get("rooms", [])),
        }
    )


def plan_rooms(project_state: dict,
               constraint_filter: AdapterResult | None = None) -> dict:
    updated = deepcopy(project_state)
    plot_w, plot_d = _plot_dimensions(updated)
    brahmasthan = _brahmasthan_bounds(plot_w, plot_d)
    # Log plot-size constraint warnings before generating rooms
    constraint_warnings = _analyze_plot_constraints(updated)
    if constraint_warnings:
        comp = updated.setdefault("compliance", {})
        comp.setdefault("compromises", []).extend(
            w for w in constraint_warnings if w.get("severity") == "warning"
        )
        comp.setdefault("compromises", []).extend(
            {"type": w["type"], "details": w["details"], "suggestion": w.get("suggestion", "")}
            for w in constraint_warnings if w.get("severity") == "info"
        )
    if updated.get("mock_llm"):
        rooms = _mock_llm_rooms(updated, constraint_filter=constraint_filter)
    else:
        rooms = _get_rooms_from_gemini(updated, brahmasthan, constraint_filter=constraint_filter)
    # Wet-area stacking enforcement: retry up to 3x if misaligned
    for attempt in range(3):
        wet_warnings = _check_wet_area_stacking(rooms)
        if not wet_warnings:
            break
        if attempt < 2:
            comp = updated.setdefault("compliance", {})
            comp.setdefault("compromises", []).append({
                "type": "wet_area_stacking",
                "details": wet_warnings,
                "attempt": attempt + 1,
            })
            if updated.get("mock_llm"):
                rooms = _mock_llm_rooms(updated, constraint_filter=constraint_filter)
            else:
                rooms = _get_rooms_from_gemini(updated, brahmasthan, constraint_filter=constraint_filter)
    validation_errors = _validate_rooms(rooms, plot_w, plot_d, brahmasthan)
    missing_rooms = _check_required_rooms(rooms, updated.get("requirements", {}))
    if validation_errors or missing_rooms:
        # For tiny plots, log missing rooms as compromises instead of failing
        plot_area = plot_w * plot_d
        if plot_area <= _TINY_PLOT_AREA and missing_rooms:
            comp = updated.setdefault("compliance", {})
            comp.setdefault("compromises", []).append({
                "type": "insufficient_plot_area",
                "details": missing_rooms,
            })
            # Remove rooms that exceed plot boundaries to clear validation
            rooms = [r for r in rooms if r.get("x", 0) >= -_EPSILON
                     and r.get("y", 0) >= -_EPSILON
                     and (r.get("x", 0) + r.get("w", 0)) <= plot_w + _EPSILON
                     and (r.get("y", 0) + r.get("h", 0)) <= plot_d + _EPSILON]
        else:
            details = validation_errors + ([f"Required rooms missing: {', '.join(missing_rooms)}"] if missing_rooms else [])
            raise ValueError("Spatial planner produced invalid rooms: " + "; ".join(details))
    updated["rooms"] = rooms
    updated["version"] = _next_version(updated)
    updated["status"] = "in_progress"
    updated["blocked_reason"] = None
    # Circulation check: log issues as compromises, don't fail the run
    circulation_issues = _check_circulation(rooms)
    if circulation_issues:
        comp = updated.setdefault("compliance", {})
        comp.setdefault("compromises", []).append({
            "type": "circulation",
            "details": circulation_issues,
        })
    _add_checkpoint(updated, "Generated initial spatial room placement from usable plot dimensions.")
    return updated

def create_canonical_spatial_plan(
    project_brief: dict,
    *,
    seed: int | None = None,
) -> dict:
    """Generate canonical spatial plan with polygon-first room geometry.

    This is the Task 2 generation-time canonical planner.  It:

    1. If input contains pre-existing rooms with geometry (polygon/vertices),
       normalizes them into canonical format.
    2. Otherwise, calls compute_room_placements() for fresh spatial placement.
    3. Attaches ventilation, doors, and suite metadata.
    4. Builds the full canonical geometry model.
    5. Returns the canonical model with geometry_authority=CANONICAL_POLYGON.

    Does NOT call plan_rooms().  Does NOT write to project_state.

    Parameters
    ----------
    project_brief : dict
        Project brief with rooms, plot, user_prompt.  If rooms already exist
        with polygon or vertices, those are used instead of generating new
        placements.
    seed : int | None
        Optional seed for deterministic placement.

    Returns
    -------
    dict
        Canonical model with rooms, walls, doors, windows, validation_findings,
        geometry_authority=CANONICAL_POLYGON, canonical_created_at_stage=SPATIAL_PLANNER.
    """
    plot_w, plot_d = _plot_dimensions(project_brief)
    brahmasthan = _brahmasthan_bounds(plot_w, plot_d)
    scale_factor = _recommended_scale(project_brief)
    space_type = project_brief.get("space_type", "residential")
    boundary_coords = project_brief.get("plot", {}).get("boundary_coords")

    input_rooms = project_brief.get("rooms", [])

    rooms_have_polygon_or_vertices = bool(input_rooms) and any(
        r.get("polygon") or r.get("vertices") for r in input_rooms
    )
    rooms_have_legacy_rectangles = bool(input_rooms) and any(
        any(r.get(k) is not None for k in ("x", "y", "w", "h"))
        for r in input_rooms
    )

    # --- Three-path routing --------------------------------------------------
    # Path A: pre-existing polygon/vertices rooms → canonicalize in-place
    # Path B: legacy rectangle-only rooms → migrate via legacy adapter
    # Path C: no existing geometry → fresh placement
    # Explicit else: both polygon and rect (mixed) → blocking validation result

    if rooms_have_polygon_or_vertices and not rooms_have_legacy_rectangles:
        # ── Path A: canonicalize existing polygon rooms ──────────────────────
        normalized = []
        for i, room in enumerate(input_rooms):
            if not room.get("polygon") and not room.get("vertices"):
                continue
            r = deepcopy(room)
            r.setdefault("canonical_id", _deterministic_id(
                "room", str(r.get("id", f"R{i}")),
                str(r.get("type", "")),
            ))
            if not r.get("id"):
                r["id"] = r["canonical_id"]
            if not r.get("polygon") and r.get("vertices"):
                r["polygon"] = [list(p) for p in r["vertices"]]
            r.setdefault("geometry_revision", 1)
            r.setdefault("identity", {})
            if r.get("source_record_id") and not r["identity"].get("source_record_id"):
                r["identity"]["source_record_id"] = r["source_record_id"]
            r.setdefault("area_sqft", _polygon_area(r.get("polygon", [])))
            r.setdefault("floor", 0)
            poly = r.get("polygon", [])
            if poly and "compatibility" not in r:
                r["compatibility"] = {}
            if poly and "bbox" not in r.get("compatibility", {}):
                xs = [p[0] for p in poly]
                ys = [p[1] for p in poly]
                r["compatibility"]["bbox"] = {
                    "x": min(xs), "y": min(ys),
                    "w": max(xs) - min(xs), "h": max(ys) - min(ys),
                    "derived": True,
                    "exact_representation": _is_rectangular_polygon(poly),
                }
            normalized.append(r)

        rooms = _attach_ventilation(normalized, plot_w, plot_d)
        rooms = _attach_doors(rooms)

        # Build minimal plan state for build_canonical_model
        plan = deepcopy(project_brief)
        plan["rooms"] = rooms
        plan["status"] = "complete"
        plan["blocked_reason"] = None

        canonical = build_canonical_model(plan)
        canonical["geometry_authority"] = "CANONICAL_POLYGON"
        # Preserve original provenance: check model-level metadata first,
        # then nested geometry_model, then per-room metadata, in priority order.
        prior_stage = (
            project_brief.get("canonical_created_at_stage")
            or project_brief.get("geometry_model", {}).get("canonical_created_at_stage")
            or _first_truthy(
                r.get("canonical_created_at_stage")
                for r in input_rooms
                if r.get("polygon") or r.get("vertices")
            )
        )
        if prior_stage:
            canonical["canonical_created_at_stage"] = prior_stage
        else:
            canonical["canonical_created_at_stage"] = "SPATIAL_PLANNER"
        canonical["legacy_adapter_used"] = False
        if "generation_metadata" not in canonical:
            canonical["generation_metadata"] = {}
        canonical["generation_metadata"]["source"] = "create_canonical_spatial_plan"
        canonical["generation_metadata"]["seed"] = seed
        canonical["generation_metadata"]["geometry_authority"] = "CANONICAL_POLYGON"
        canonical["generation_metadata"]["canonical_created_at_stage"] = canonical["canonical_created_at_stage"]

    elif rooms_have_legacy_rectangles and not rooms_have_polygon_or_vertices:
        # ── Path B: legacy rectangle-only → migrate via legacy adapter ───────
        # Rectangle coordinates are synthesized into polygon vertices by
        # legacy_state_to_canonical_model; geometry is NOT regenerated fresh.
        canonical = legacy_state_to_canonical_model(project_brief)
        # Override stage to SPATIAL_PLANNER for consistency (the legacy
        # adapter sets LEGACY_MIGRATION internally; caller can inspect both).
        canonical["canonical_created_at_stage"] = "LEGACY_MIGRATION"
        canonical["legacy_adapter_used"] = True
        if "generation_metadata" not in canonical:
            canonical["generation_metadata"] = {}
        canonical["generation_metadata"]["source"] = "create_canonical_spatial_plan"
        canonical["generation_metadata"]["seed"] = seed
        canonical["generation_metadata"]["legacy_migration"] = True

    elif not input_rooms:
        # ── Path C: no existing geometry → fresh placement ───────────────────
        raw_placed = compute_room_placements(
            plot_w, plot_d,
            project_brief.get("requirements", {}),
            brahmasthan,
            scale_factor=scale_factor,
            boundary_coords=boundary_coords,
            space_type=space_type,
            preserved_walls=project_brief.get("requirements", {}).get("preserved_walls"),
        )
        normalized = [_normalize_room(room, i) for i, room in enumerate(raw_placed)]
        rooms = _attach_ventilation(normalized, plot_w, plot_d)
        rooms = _attach_doors(rooms)
        rooms = _apply_suite_metadata(rooms)

        plan = deepcopy(project_brief)
        plan["rooms"] = rooms
        plan["status"] = "complete"
        plan["blocked_reason"] = None

        canonical = build_canonical_model(plan)
        canonical["geometry_authority"] = "CANONICAL_POLYGON"
        canonical["canonical_created_at_stage"] = "SPATIAL_PLANNER"
        canonical["legacy_adapter_used"] = False
        if "generation_metadata" not in canonical:
            canonical["generation_metadata"] = {}
        canonical["generation_metadata"]["source"] = "create_canonical_spatial_plan"
        canonical["generation_metadata"]["seed"] = seed
        canonical["generation_metadata"]["geometry_authority"] = "CANONICAL_POLYGON"
        canonical["generation_metadata"]["canonical_created_at_stage"] = "SPATIAL_PLANNER"

    else:
        # ── Blocking: mixed polygon/vertices AND rectangle keys ─────────────
        # The caller supplied both polygon/vertices AND x/y/w/h on the same
        # room(s).  This is ambiguous — polygon wins but the conflict must
        # be surfaced.
        canonical = {
            "geometry_authority": "CANONICAL_POLYGON",
            "canonical_created_at_stage": "BLOCKED_VALIDATION",
            "legacy_adapter_used": False,
            "geometry_valid": False,
            "validation_findings": [_normalize_finding(
                "POLYGON_RECT_MISMATCH", "ERROR", "project_brief", [],
                "Input rooms have both polygon/vertices and x/y/w/h keys. "
                "This ambiguous representation cannot be automatically resolved. "
                "Remove one representation before calling create_canonical_spatial_plan.",
            )],
        }
        return canonical

    # Ensure geometry_revision and compatibility.bbox on all rooms
    for room in canonical.get("rooms", []):
        room.setdefault("geometry_revision", 1)
        if "compatibility" not in room:
            room["compatibility"] = {}
        if "bbox" not in room.get("compatibility", {}):
            poly = room.get("polygon", [])
            if len(poly) >= 3:
                xs = [p[0] for p in poly]
                ys = [p[1] for p in poly]
                room["compatibility"]["bbox"] = {
                    "x": min(xs), "y": min(ys),
                    "w": max(xs) - min(xs), "h": max(ys) - min(ys),
                    "derived": True,
                    "exact_representation": _is_rectangular_polygon(poly),
                }

    return canonical


def _first_truthy(iterable):
    """Return the first truthy value from *iterable*, or None."""
    for v in iterable:
        if v:
            return v
    return None


def _is_rectangular_polygon(polygon: list) -> bool:
    """Check if a polygon is a rectangle (4 vertices, right angles)."""
    if len(polygon) != 4:
        return False
    # Check if the polygon is an axis-aligned rectangle
    xs = sorted(set(round(p[0], 6) for p in polygon))
    ys = sorted(set(round(p[1], 6) for p in polygon))
    if len(xs) != 2 or len(ys) != 2:
        return False
    # Check all 4 corners are present
    corners = {(xs[0], ys[0]), (xs[1], ys[0]), (xs[1], ys[1]), (xs[0], ys[1])}
    poly_corners = {(round(p[0], 6), round(p[1], 6)) for p in polygon}
    return corners == poly_corners

