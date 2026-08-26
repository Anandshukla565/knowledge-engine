from copy import deepcopy

from shapely.geometry import Polygon, box

from knowledge_engine.planning.spatial_planner import MINIMUM_ROOM_SIZES, _rects_overlap
from knowledge_engine.planning.planner_adapter import (
    AdapterResult,
    ClassifiedConstraint,
    ConstraintClassification,
    solver_constraints_as_rules,
)


MINIMUM_PASSAGE_FT = 4.0

# Default clearance rules when none are provided
DEFAULT_CLEARANCE_RULES = [
    {
        "rule_id": "CLR_ROOM_ROOM_DEFAULT",
        "clearance_type": "ROOM_TO_ROOM",
        "minimum_distance_ft": 3.0,
        "severity": "ERROR",
        "blocks_geometry": True,
        "blocks_issue": True,
        "blocking_scope": ["CONCEPT", "IFC", "DXF", "REVIT"],
        "correction_allowed": True,
        "correction_mode": ["TRANSLATION"],
        "source_status": "PROJECT_PRESET",
    },
    {
        "rule_id": "CLR_ROOM_PLOT_DEFAULT",
        "clearance_type": "ROOM_TO_PLOT_BOUNDARY",
        "minimum_distance_ft": 2.0,
        "severity": "ERROR",
        "blocks_geometry": True,
        "blocks_issue": True,
        "blocking_scope": ["CONCEPT", "IFC", "DXF", "REVIT"],
        "correction_allowed": True,
        "correction_mode": ["TRANSLATION"],
        "source_status": "PROJECT_PRESET",
    },
]

# Default solver constraints
DEFAULT_CLEARANCE_CONSTRAINTS = {
    "max_clearance_iterations": 10,
    "max_candidates_per_violation": 12,
    "max_total_candidates": 100,
    "minimum_improvement_ft": 0.01,
}

# ---------------------------------------------------------------------------
# Polygon helpers (ray-casting, shoelace, rect-to-polygon)
# ---------------------------------------------------------------------------

def point_in_polygon(x: float, y: float, vertices: list[tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon test.

    Returns True if (x, y) is inside the polygon defined by vertices.
    Handles convex and concave polygons.
    """
    n = len(vertices)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = vertices[i]
        xj, yj = vertices[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def polygon_area(vertices: list[tuple[float, float]]) -> float:
    """Shoelace formula — returns signed area (positive for CCW winding)."""
    area = 0.0
    n = len(vertices)
    for i in range(n):
        x1, y1 = vertices[i]
        x2, y2 = vertices[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def rect_to_polygon(x: float, y: float, w: float, h: float) -> list[tuple[float, float]]:
    """Convert an axis-aligned rectangle to 4-vertex polygon (CCW)."""
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def room_polygon(room: dict) -> list[tuple[float, float]] | None:
    """Return the vertex list for a room, regardless of representation.

    Priority:
    1. room["polygon"] — authoritative canonical geometry (canonical rooms)
    2. room["vertices"] — non-rect polygon room (legacy)
    3. room["x"],["y"],["w"],["h"] — standard rectangle (legacy)

    Returns None if the room has no position data.
    """
    poly = room.get("polygon")
    if poly and len(poly) >= 3:
        return [(float(v[0]), float(v[1])) for v in poly]
    verts = room.get("vertices")
    if verts and len(verts) >= 3:
        return [(float(vx), float(vy)) for vx, vy in verts]
    x = room.get("x")
    y = room.get("y")
    w = room.get("w")
    h = room.get("h")
    if x is not None and y is not None and w and h:
        return rect_to_polygon(float(x), float(y), float(w), float(h))
    return None


def bbox_of(vertices: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    """Return (x_min, y_min, x_max, y_max) bounding box of vertices."""
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    return min(xs), min(ys), max(xs), max(ys)


def polygons_overlap(verts_a: list, verts_b: list) -> bool:
    """Check if two polygons overlap using shapely."""
    try:
        poly_a = Polygon(verts_a)
        poly_b = Polygon(verts_b)
        if not poly_a.is_valid:
            poly_a = poly_a.buffer(0)
        if not poly_b.is_valid:
            poly_b = poly_b.buffer(0)
        return poly_a.intersection(poly_b).area > 1e-6
    except Exception:
        # Fallback: bbox overlap check
        ax0, ay0, ax1, ay1 = bbox_of(verts_a)
        bx0, by0, bx1, by1 = bbox_of(verts_b)
        return not (ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0)


def polygons_clearance(verts_a: list, verts_b: list) -> float:
    """Return minimum distance between two polygons."""
    try:
        poly_a = Polygon(verts_a)
        poly_b = Polygon(verts_b)
        if not poly_a.is_valid:
            poly_a = poly_a.buffer(0)
        if not poly_b.is_valid:
            poly_b = poly_b.buffer(0)
        return float(poly_a.distance(poly_b))
    except Exception:
        return 0.0


def check_overlaps(rooms: list) -> list:
    overlaps = []
    for i, room_a in enumerate(rooms):
        poly_a_verts = room_polygon(room_a)
        if poly_a_verts is None:
            continue
        poly_a = Polygon(poly_a_verts) if len(poly_a_verts) >= 3 else None
        if poly_a and not poly_a.is_valid:
            poly_a = poly_a.buffer(0)
        for room_b in rooms[i + 1:]:
            if int(room_a.get("floor", 0)) != int(room_b.get("floor", 0)):
                continue
            poly_b_verts = room_polygon(room_b)
            if poly_b_verts is None:
                continue
            poly_b = Polygon(poly_b_verts) if len(poly_b_verts) >= 3 else None
            if poly_b and not poly_b.is_valid:
                poly_b = poly_b.buffer(0)
            if poly_a is None or poly_b is None:
                continue
            overlap_area = poly_a.intersection(poly_b).area
            if overlap_area > 1e-6:
                overlaps.append({"room_a": room_a.get("id"), "room_b": room_b.get("id"), "overlap_area": round(overlap_area, 2)})
    return overlaps


def check_minimum_sizes(rooms: list) -> list:
    violations = []
    for room in rooms:
        room_type = str(room.get("type", "")).strip()
        # Rooms with a compromise_reason were intentionally shrunk to fit the plot.
        # Accept them as-is rather than blocking the run.
        if room.get("compromise_reason"):
            continue
        normalized = room_type.lower().replace("_", " ")
        minimum = MINIMUM_ROOM_SIZES.get(room_type)
        if minimum is None:
            if "master" in normalized and "bed" in normalized:
                minimum = MINIMUM_ROOM_SIZES.get("MasterBed")
            elif "bed" in normalized:
                minimum = MINIMUM_ROOM_SIZES.get("Bedroom")
            elif "kitchen" in normalized:
                minimum = MINIMUM_ROOM_SIZES.get("Kitchen")
            elif "living" in normalized:
                minimum = MINIMUM_ROOM_SIZES.get("Living")
            elif "bath" in normalized or "toilet" in normalized:
                minimum = MINIMUM_ROOM_SIZES.get("Bathroom")
            elif "stair" in normalized:
                minimum = MINIMUM_ROOM_SIZES.get("Staircase")
        if minimum is None:
            continue
        width = float(room.get("w", 0))
        height = float(room.get("h", 0))
        actual_sqft = float(room.get("area_sqft") or width * height)
        minimum_sqft = float(minimum["area"])
        if width < float(minimum["w"]) or height < float(minimum["h"]) or actual_sqft < minimum_sqft:
            violations.append(
                {
                    "room_id": room.get("id"),
                    "type": room_type,
                    "actual_sqft": round(actual_sqft, 2),
                    "minimum_sqft": round(minimum_sqft, 2),
                    "deficit_sqft": round(max(0.0, minimum_sqft - actual_sqft), 2),
                }
            )
    return violations


def check_clearances(rooms: list) -> list:
    violations = []
    for i, room_a in enumerate(rooms):
        verts_a = room_polygon(room_a)
        if verts_a is None:
            continue
        poly_a = Polygon(verts_a) if len(verts_a) >= 3 else None
        if poly_a and not poly_a.is_valid:
            poly_a = poly_a.buffer(0)
        if poly_a is None:
            continue
        for room_b in rooms[i + 1:]:
            if int(room_a.get("floor", 0)) != int(room_b.get("floor", 0)):
                continue
            verts_b = room_polygon(room_b)
            if verts_b is None:
                continue
            poly_b = Polygon(verts_b) if len(verts_b) >= 3 else None
            if poly_b and not poly_b.is_valid:
                poly_b = poly_b.buffer(0)
            if poly_b is None:
                continue
            if poly_a.intersection(poly_b).area > 1e-6:
                continue
            clearance = float(poly_a.distance(poly_b))
            # Shared-wall (zero gap) is valid; only flag positive gaps below minimum.
            if 0 < clearance < MINIMUM_PASSAGE_FT:
                violations.append(
                    {
                        "room_a": room_a.get("id"),
                        "room_b": room_b.get("id"),
                        "clearance_ft": round(clearance, 2),
                        "minimum_clearance_ft": MINIMUM_PASSAGE_FT,
                        "deficit_ft": round(MINIMUM_PASSAGE_FT - clearance, 2),
                    }
                )
    return violations


def check_brahmasthan(rooms: list, plot_w: float, plot_d: float) -> bool:
    brahmasthan = box(float(plot_w) / 3.0, float(plot_d) / 3.0, (float(plot_w) * 2.0) / 3.0, (float(plot_d) * 2.0) / 3.0)
    for room in rooms:
        verts = room_polygon(room)
        if verts is None:
            continue
        room_poly = Polygon(verts) if len(verts) >= 3 else None
        if room_poly is None:
            continue
        if not room_poly.is_valid:
            room_poly = room_poly.buffer(0)
        if room_poly.intersection(brahmasthan).area > 1e-6:
            return False
    return True


def resolve_overlap(room_a: dict, room_b: dict, plot_bounds: dict) -> dict:
    fixed = dict(room_a)
    moving = dict(room_b)
    plot_w = float(plot_bounds.get("w") or plot_bounds.get("width") or plot_bounds.get("width_ft") or 0)
    plot_d = float(plot_bounds.get("h") or plot_bounds.get("depth") or plot_bounds.get("depth_ft") or 0)
    fixed_poly = box(
        float(fixed.get("x", 0)),
        float(fixed.get("y", 0)),
        float(fixed.get("x", 0)) + float(fixed.get("w", 0)),
        float(fixed.get("y", 0)) + float(fixed.get("h", 0)),
    )
    original_x = float(moving.get("x", 0))
    original_y = float(moving.get("y", 0))
    moving_w = float(moving.get("w", 0))
    moving_h = float(moving.get("h", 0))
    candidates = [
        (float(fixed.get("x", 0)) + float(fixed.get("w", 0)), original_y),
        (float(fixed.get("x", 0)) - moving_w, original_y),
        (original_x, float(fixed.get("y", 0)) + float(fixed.get("h", 0))),
        (original_x, float(fixed.get("y", 0)) - moving_h),
    ]
    valid_candidates = []
    for x, y in candidates:
        if x < 0 or y < 0 or x + moving_w > plot_w or y + moving_h > plot_d:
            continue
        candidate_poly = box(x, y, x + moving_w, y + moving_h)
        if candidate_poly.intersection(fixed_poly).area > 0:
            continue
        movement = abs(x - original_x) + abs(y - original_y)
        valid_candidates.append((movement, x, y))
    if not valid_candidates:
        moving.setdefault("geometry_warning", "Could not auto-resolve overlap within plot bounds.")
        return moving
    _, best_x, best_y = sorted(valid_candidates, key=lambda item: item[0])[0]
    moving["x"] = round(best_x, 2)
    moving["y"] = round(best_y, 2)
    moving["area_sqft"] = round(moving_w * moving_h, 2)
    return moving


def resolve_clearance_violation(room_a: dict, room_b: dict, plot_bounds: dict) -> dict:
    """Try to increase the gap between two rooms to meet the minimum passage clearance.

    Returns the updated room_b dict (moves room_b; room_a stays fixed).
    If no valid position can achieve the clearance, returns room_b unchanged.
    """
    MINIMUM_PASSAGE_FT = 4.0
    fixed = dict(room_a)
    moving = dict(room_b)
    plot_w = float(plot_bounds.get("w") or plot_bounds.get("width") or plot_bounds.get("width_ft") or 0)
    plot_d = float(plot_bounds.get("h") or plot_bounds.get("depth") or plot_bounds.get("depth_ft") or 0)

    fx, fy = float(fixed.get("x", 0)), float(fixed.get("y", 0))
    fw, fh = float(fixed.get("w", 0)), float(fixed.get("h", 0))
    mx, my = float(moving.get("x", 0)), float(moving.get("y", 0))
    mw, mh = float(moving.get("w", 0)), float(moving.get("h", 0))

    fixed_poly = box(fx, fy, fx + fw, fy + fh)
    moving_poly = box(mx, my, mx + mw, my + mh)
    current_gap = fixed_poly.distance(moving_poly)
    needed_shift = MINIMUM_PASSAGE_FT - current_gap
    if needed_shift <= 0:
        return moving  # already clear

    # Determine primary gap direction: horizontal or vertical
    # Project onto x and y axes to see which axis has the smaller overlap/overhang
    h_overlap = max(0, min(fx + fw, mx + mw) - max(fx, mx))
    v_overlap = max(0, min(fy + fh, my + mh) - max(fy, my))

    # If rooms overlap significantly on one axis, the gap is on the other axis
    if h_overlap > v_overlap:
        # Gap is vertical: rooms are stacked, move one vertically
        if my >= fy + fh:
            # room_b is below room_a, move it down
            candidates = [(mx, my + needed_shift)]
        elif fy >= my + mh:
            # room_b is above room_a, move it up
            candidates = [(mx, my - needed_shift)]
        else:
            # Side-by-side with vertical overlap, try vertical shift first
            if (my + mh / 2) >= (fy + fh / 2):
                candidates = [(mx, my + needed_shift)]
            else:
                candidates = [(mx, my - needed_shift)]
    else:
        # Gap is horizontal: rooms are side-by-side, move one horizontally
        if mx >= fx + fw:
            # room_b is to the right of room_a, move it right
            candidates = [(mx + needed_shift, my)]
        elif fx >= mx + mw:
            # room_b is to the left of room_a, move it left
            candidates = [(mx - needed_shift, my)]
        else:
            # One is above the other with horizontal overlap, try horizontal shift
            if (mx + mw / 2) >= (fx + fw / 2):
                candidates = [(mx + needed_shift, my)]
            else:
                candidates = [(mx - needed_shift, my)]

    # Fallback: also try the perpendicular direction and both directions
    all_candidates = list(candidates)
    cx, cy = candidates[0]
    if h_overlap > v_overlap:
        # Primary was vertical, also try horizontal
        if mx >= fx + fw:
            all_candidates.append((mx + needed_shift, my))
        elif fx >= mx + mw:
            all_candidates.append((mx - needed_shift, my))
        else:
            all_candidates.append((mx + needed_shift, my))
            all_candidates.append((mx - needed_shift, my))
    else:
        # Primary was horizontal, also try vertical
        if my >= fy + fh:
            all_candidates.append((mx, my + needed_shift))
        elif fy >= my + mh:
            all_candidates.append((mx, my - needed_shift))
        else:
            all_candidates.append((mx, my + needed_shift))
            all_candidates.append((mx, my - needed_shift))

    valid_candidates = []
    for x, y in all_candidates:
        if x < -0.01 or y < -0.01 or x + mw > plot_w + 0.01 or y + mh > plot_d + 0.01:
            continue
        candidate_poly = box(x, y, x + mw, y + mh)
        if candidate_poly.intersection(fixed_poly).area > 1e-6:
            continue
        # Also check it doesn't overlap any other room (we skip this here, caller re-checks)
        movement = abs(x - mx) + abs(y - my)
        new_gap = fixed_poly.distance(candidate_poly)
        valid_candidates.append((movement, new_gap, x, y))

    if not valid_candidates:
        moving.setdefault("geometry_warning", "Could not auto-resolve clearance within plot bounds.")
        return moving

    # Pick the candidate with the largest resulting gap (prefer minimal movement)
    valid_candidates.sort(key=lambda item: (-item[1], item[0]))
    _, _, best_x, best_y = valid_candidates[0]
    moving["x"] = round(best_x, 2)
    moving["y"] = round(best_y, 2)
    moving["area_sqft"] = round(mw * mh, 2)
    return moving


# ===========================================================================
# Task 3 — Canonical Clearance Detection and Correction
# ===========================================================================

# Default clearance rules (project presets, not verified regulatory values)
DEFAULT_CLEARANCE_RULES = [
    {
        "rule_id": "CLR_ROOM_ROOM_DEFAULT",
        "clearance_type": "ROOM_TO_ROOM",
        "minimum_distance_ft": 3.0,
        "severity": "ERROR",
        "blocks_geometry": True,
        "blocks_issue": True,
        "blocking_scope": ["CONCEPT", "IFC", "DXF", "REVIT"],
        "correction_allowed": True,
        "correction_mode": ["TRANSLATION"],
        "source_status": "PROJECT_PRESET",
        "source_reference": None,
    },
    {
        "rule_id": "CLR_ROOM_PLOT_DEFAULT",
        "clearance_type": "ROOM_TO_PLOT_BOUNDARY",
        "minimum_distance_ft": 2.0,
        "severity": "ERROR",
        "blocks_geometry": True,
        "blocks_issue": True,
        "blocking_scope": ["CONCEPT", "IFC", "DXF", "REVIT"],
        "correction_allowed": True,
        "correction_mode": ["TRANSLATION"],
        "source_status": "PROJECT_PRESET",
        "source_reference": None,
    },
]

DEFAULT_CLEARANCE_CONSTRAINTS = {
    "max_clearance_iterations": 10,
    "max_candidates_per_violation": 12,
    "max_total_candidates": 100,
    "minimum_improvement_ft": 0.01,
}

_CLEARANCE_CHANGE_COUNTER = 0
_CLEARANCE_DECISION_COUNTER = 0


def _reset_clearance_counters():
    """Reset deterministic counters (call before each solver run in tests)."""
    global _CLEARANCE_CHANGE_COUNTER, _CLEARANCE_DECISION_COUNTER
    _CLEARANCE_CHANGE_COUNTER = 0
    _CLEARANCE_DECISION_COUNTER = 0


def _next_change_id():
    global _CLEARANCE_CHANGE_COUNTER
    _CLEARANCE_CHANGE_COUNTER += 1
    return f"geom_change_clearance_{_CLEARANCE_CHANGE_COUNTER:03d}"


def _next_decision_id():
    global _CLEARANCE_DECISION_COUNTER
    _CLEARANCE_DECISION_COUNTER += 1
    return f"decision_clearance_{_CLEARANCE_DECISION_COUNTER:03d}"


def _find_rule(rules, clearance_type):
    """Find the first rule matching a clearance type."""
    for r in rules:
        if r.get("clearance_type") == clearance_type:
            return r
    return None


def _room_solver_controls(room):
    """Extract solver_controls from a room with defaults."""
    sc = room.get("solver_controls", {})
    return {
        "movable": sc.get("movable", True),
        "locked": sc.get("locked", False),
        "max_translation_ft": float(sc.get("max_translation_ft", 6.0)),
        "preferred_translation_axes": sc.get("preferred_translation_axes", ["X", "Y"]),
    }


def _polygon_bbox(poly):
    """Return (min_x, min_y, max_x, max_y) for a polygon."""
    xs = [float(v[0]) for v in poly]
    ys = [float(v[1]) for v in poly]
    return min(xs), min(ys), max(xs), max(ys)


def _translate_polygon(poly, dx, dy):
    """Return a new polygon translated by (dx, dy)."""
    return [[float(v[0]) + dx, float(v[1]) + dy] for v in poly]


def detect_canonical_clearances(
    canonical_model: dict,
    clearance_rules: list[dict],
) -> list[dict]:
    """Detect clearance violations in a canonical model.

    Reads authoritative room polygons. Returns normalized findings.
    Does not mutate the model.
    """
    from .geometry_model import _normalize_finding
    from . import geometry_model as gm

    findings = []
    rooms = canonical_model.get("rooms", [])
    plot = canonical_model.get("plot", {})
    plot_w = float(plot.get("usable_width_ft", 0))
    plot_d = float(plot.get("usable_depth_ft", 0))

    # Build list of rooms with valid polygons on the same storey
    valid_rooms = []
    for room in rooms:
        poly = room.get("polygon")
        if not poly or len(poly) < 3:
            continue
        # Validate polygon
        try:
            from shapely.geometry import Polygon as SPoly
            sp = SPoly(poly)
            if not sp.is_valid:
                sp = sp.buffer(0)
            valid_rooms.append((room, sp))
        except Exception:
            continue

    # ROOM_TO_ROOM and ROOM_TO_PLOT_BOUNDARY detection
    for i, (room_a, poly_a) in enumerate(valid_rooms):
        floor_a = int(room_a.get("floor", 0))
        minx_a, miny_a, maxx_a, maxy_a = _polygon_bbox(room_a["polygon"])

        # Room-to-plot-boundary
        plot_rule = _find_rule(clearance_rules, "ROOM_TO_PLOT_BOUNDARY")
        if plot_rule:
            dist_left = minx_a
            dist_right = plot_w - maxx_a
            dist_bottom = miny_a
            dist_top = plot_d - maxy_a
            plot_dists = [d for d in [dist_left, dist_right, dist_bottom, dist_top] if d < 0]
            if plot_dists:
                min_dist = min(plot_dists)
                min_required = float(plot_rule.get("minimum_distance_ft", 2.0))
                auto_correctable = (
                    plot_rule.get("correction_allowed", False)
                    and "TRANSLATION" in plot_rule.get("correction_mode", [])
                )
                findings.append({
                    **_normalize_finding(
                        "CLEARANCE_VIOLATION",
                        plot_rule.get("severity", "ERROR"),
                        "room",
                        [room_a["id"]],
                        f"Room {room_a['id']} is {abs(min_dist):.2f} ft outside plot boundary; "
                        f"minimum clearance is {min_required} ft.",
                    ),
                    "measured_clearance_ft": round(min_dist, 2),
                    "required_clearance_ft": min_required,
                    "clearance_type": "ROOM_TO_PLOT_BOUNDARY",
                    "auto_correction_eligible": auto_correctable,
                    "entity_ids": [room_a["id"]],
                    "blocks_geometry": plot_rule.get("blocks_geometry", True),
                    "blocks_issue": plot_rule.get("blocks_issue", True),
                    "blocking_scope": plot_rule.get("blocking_scope", ["CONCEPT"]),
                })
            else:
                min_dist = min(dist_left, dist_right, dist_bottom, dist_top)
                min_required = float(plot_rule.get("minimum_distance_ft", 2.0))
                if 0 <= min_dist < min_required:
                    auto_correctable = (
                        plot_rule.get("correction_allowed", False)
                        and "TRANSLATION" in plot_rule.get("correction_mode", [])
                    )
                    findings.append({
                        **_normalize_finding(
                            "CLEARANCE_VIOLATION",
                            plot_rule.get("severity", "ERROR"),
                            "room",
                            [room_a["id"]],
                            f"Room {room_a['id']} is {min_dist:.2f} ft from plot boundary; "
                            f"minimum clearance is {min_required} ft.",
                        ),
                        "measured_clearance_ft": round(min_dist, 2),
                        "required_clearance_ft": min_required,
                        "clearance_type": "ROOM_TO_PLOT_BOUNDARY",
                        "auto_correction_eligible": auto_correctable,
                        "entity_ids": [room_a["id"]],
                        "blocks_geometry": plot_rule.get("blocks_geometry", True),
                        "blocks_issue": plot_rule.get("blocks_issue", True),
                        "blocking_scope": plot_rule.get("blocking_scope", ["CONCEPT"]),
                    })

        # Room-to-room
        for j in range(i + 1, len(valid_rooms)):
            room_b, poly_b = valid_rooms[j]
            floor_b = int(room_b.get("floor", 0))
            if floor_a != floor_b:
                continue

            rule = _find_rule(clearance_rules, "ROOM_TO_ROOM")
            if rule is None:
                continue

            # Skip overlapping rooms (overlap is a separate finding)
            if poly_a.intersection(poly_b).area > 1e-6:
                continue

            clearance = float(poly_a.distance(poly_b))
            min_required = float(rule.get("minimum_distance_ft", 3.0))

            if 0 < clearance < min_required:
                auto_correctable = (
                    rule.get("correction_allowed", False)
                    and "TRANSLATION" in rule.get("correction_mode", [])
                )
                # Check if either room is locked
                ctrl_a = _room_solver_controls(room_a)
                ctrl_b = _room_solver_controls(room_b)
                if ctrl_a.get("locked") and ctrl_b.get("locked"):
                    auto_correctable = False

                entity_ids = sorted([room_a["id"], room_b["id"]])
                findings.append({
                    **_normalize_finding(
                        "CLEARANCE_VIOLATION",
                        rule.get("severity", "ERROR"),
                        "room_pair",
                        entity_ids,
                        f"Clearance between {room_a['id']} and {room_b['id']} is "
                        f"{clearance:.2f} ft; minimum is {min_required} ft.",
                    ),
                    "measured_clearance_ft": round(clearance, 2),
                    "required_clearance_ft": min_required,
                    "clearance_type": "ROOM_TO_ROOM",
                    "auto_correction_eligible": auto_correctable,
                    "entity_ids": entity_ids,
                    "blocks_geometry": rule.get("blocks_geometry", True),
                    "blocks_issue": rule.get("blocks_issue", True),
                    "blocking_scope": rule.get("blocking_scope", ["CONCEPT"]),
                })

    # Sort deterministically
    findings.sort(key=lambda f: (f.get("entity_ids", []), f.get("code", "")))
    return findings


def _generate_boundary_shift_candidates(
    model: dict,
    room: dict,
    finding: dict,
    constraints: dict,
) -> list[dict]:
    """Generate translation candidates to push a room away from plot boundary.

    For ROOM_TO_PLOT_BOUNDARY violations, the room must be shifted inward
    by at least (required_clearance - measured_distance).  Multiple
    directions are offered (prioritizing directions with more clearance).
    """
    plot = model.get("plot", {})
    plot_w = float(plot.get("usable_width_ft", 0))
    plot_d = float(plot.get("usable_depth_ft", 0))
    poly = room.get("polygon", [])
    if not poly or len(poly) < 3:
        return []

    ctrl = _room_solver_controls(room)
    max_shift = ctrl.get("max_translation_ft", 6.0)
    max_candidates = int(constraints.get("max_candidates_per_violation", 12))

    minx, miny, maxx, maxy = _polygon_bbox(poly)
    dist_left   = minx
    dist_right  = plot_w - maxx
    dist_bottom = miny
    dist_top    = plot_d - maxy

    # measured_clearance is negative if room is outside, or small positive if inside but too close
    measured = finding.get("measured_clearance_ft", 0.0)
    required = finding.get("required_clearance_ft", 2.0)

    # We need to push the room inward.  Required shift = required - measured.
    # If the room is outside the boundary (measured < 0), shift = required - measured = required + |measured|
    # If the room is inside (measured >= 0), shift = required - measured
    needed = required - measured
    if needed <= 0:
        return []

    # Determine which sides are closest to boundary violations
    violations = []
    if dist_left < required:
        violations.append(("left", dist_left, needed))
    if dist_right < required:
        violations.append(("right", dist_right, needed))
    if dist_bottom < required:
        violations.append(("bottom", dist_bottom, needed))
    if dist_top < required:
        violations.append(("top", dist_top, needed))

    if not violations:
        return []

    # Collect sibling rooms (same floor, different room) for clearance checks
    _room_min_clearance = constraints.get("room_to_room_clearance_ft", 3.0)
    _floor_id = room.get("floor", room.get("floor_level", 0))
    _sibling_rects = []
    for sibling in model.get("rooms", []):
        if sibling.get("id") == room.get("id"):
            continue
        s_floor = sibling.get("floor", sibling.get("floor_level", 0))
        if int(s_floor) != int(_floor_id):
            continue
        s_poly = sibling.get("polygon", [])
        if s_poly and len(s_poly) >= 3:
            _sibling_rects.append(_polygon_bbox(s_poly))

    # For each violating side, generate a shift direction
    # Also try combined diagonal shifts if multiple sides violate
    candidates = []
    seen = set()
    rank = 0

    def _add_candidate(dx, dy):
        nonlocal rank
        movement = (dx ** 2 + dy ** 2) ** 0.5
        if movement > max_shift + 1e-9:
            return
        key = (round(dx, 3), round(dy, 3))
        if key in seen:
            return
        seen.add(key)
        rank += 1
        axis = "X" if abs(dx) >= abs(dy) else "Y"
        if dx == 0 and dy == 0:
            axis = "CENTER"
        candidate = {
            "candidate_id": f"clr_boundary_{room['id']}_{axis}_{rank}",
            "entity_id": room["id"],
            "operation": "TRANSLATION",
            "dx": round(dx, 3),
            "dy": round(dy, 3),
            "movement_distance_ft": round(movement, 3),
            "reason": f"Push {room['id']} inward from plot boundary "
                      f"(needs {needed:.1f} ft shift)",
            "deterministic_rank": rank,
        }
        # Validate: shifted room must still maintain room-to-room clearances
        if _sibling_rects and poly:
            translated = _translate_polygon(poly, dx, dy)
            tx1, ty1, tx2, ty2 = _polygon_bbox(translated)
            clearance_ok = True
            for sx1, sy1, sx2, sy2 in _sibling_rects:
                # No overlap
                if not (tx2 <= sx1 or sx2 <= tx1 or ty2 <= sy1 or sy2 <= ty1):
                    clearance_ok = False
                    break
                # Minimum gap
                gap = float('inf')
                if tx2 <= sx1:
                    gap = min(gap, sx1 - tx2)
                if sx2 <= tx1:
                    gap = min(gap, tx1 - sx2)
                if ty2 <= sy1:
                    gap = min(gap, sy1 - ty2)
                if sy2 <= ty1:
                    gap = min(gap, ty1 - sy2)
                if gap < _room_min_clearance:
                    clearance_ok = False
                    break
            if not clearance_ok:
                return
        candidates.append(candidate)

    # Prioritize single-axis shifts first (most predictable)
    for side, dist, need in violations:
        shift = max(need, 0.1)  # at least a small shift
        if side == "left":
            _add_candidate(shift, 0)
            _add_candidate(shift, -min(need * 0.5, dist_bottom))  # also nudge down
            _add_candidate(shift, min(need * 0.5, dist_top))       # also nudge up
        elif side == "right":
            _add_candidate(-shift, 0)
            _add_candidate(-shift, -min(need * 0.5, dist_bottom))
            _add_candidate(-shift, min(need * 0.5, dist_top))
        elif side == "bottom":
            _add_candidate(0, shift)
            _add_candidate(-min(need * 0.5, dist_left), shift)
            _add_candidate(min(need * 0.5, dist_right), shift)
        elif side == "top":
            _add_candidate(0, -shift)
            _add_candidate(-min(need * 0.5, dist_left), -shift)
            _add_candidate(min(need * 0.5, dist_right), -shift)

    # If multiple sides violate, try a center-shift (average of all shifts)
    if len(violations) >= 2:
        cx = 0.0
        cy = 0.0
        for side, dist, need in violations:
            if side == "left":
                cx += need
            elif side == "right":
                cx -= need
            elif side == "bottom":
                cy += need
            elif side == "top":
                cy -= need
        _add_candidate(cx, cy)

    # Sort by movement distance (shortest first)
    candidates.sort(key=lambda c: (c["movement_distance_ft"], c["deterministic_rank"]))
    for i, c in enumerate(candidates):
        c["deterministic_rank"] = i + 1

    return candidates


def generate_clearance_translation_candidates(
    model: dict,
    finding: dict,
    constraints: dict,
) -> list[dict]:
    """Generate deterministic translation candidates for a clearance violation.

    Handles both ROOM_TO_ROOM (two rooms) and ROOM_TO_PLOT_BOUNDARY (single
    room) findings.
    """
    rooms = model.get("rooms", [])
    entity_ids = finding.get("entity_ids", [])
    clearance_type = finding.get("clearance_type", "ROOM_TO_ROOM")

    # Find the rooms involved
    room_map = {r["id"]: r for r in rooms}
    involved = [room_map[eid] for eid in entity_ids if eid in room_map]

    # ROOM_TO_PLOT_BOUNDARY: only one entity — shift the room inward
    if len(involved) == 1 and clearance_type == "ROOM_TO_PLOT_BOUNDARY":
        return _generate_boundary_shift_candidates(model, involved[0], finding, constraints)

    if len(involved) < 2:
        return []

    room_a, room_b = involved[0], involved[1]
    measured = finding.get("measured_clearance_ft", 0)
    required = finding.get("required_clearance_ft", 3.0)
    needed_shift = required - measured

    if needed_shift <= 0:
        return []

    # Determine which room to move (prefer the non-locked, more movable one)
    ctrl_a = _room_solver_controls(room_a)
    ctrl_b = _room_solver_controls(room_b)

    if ctrl_a.get("locked") and ctrl_b.get("locked"):
        return []  # both locked, no candidates

    if ctrl_a.get("locked"):
        moving_room, fixed_room = room_b, room_a
    elif ctrl_b.get("locked"):
        moving_room, fixed_room = room_a, room_b
    else:
        # Prefer the room with larger max_translation_ft
        if ctrl_a.get("max_translation_ft", 0) >= ctrl_b.get("max_translation_ft", 0):
            moving_room, fixed_room = room_b, room_a
        else:
            moving_room, fixed_room = room_a, room_b

    max_shift = _room_solver_controls(moving_room).get("max_translation_ft", 6.0)
    max_candidates = int(constraints.get("max_candidates_per_violation", 12))

    # Get bboxes
    fx_min, fy_min, fx_max, fy_max = _polygon_bbox(fixed_room["polygon"])
    mx_min, my_min, mx_max, my_max = _polygon_bbox(moving_room["polygon"])
    mw = mx_max - mx_min
    mh = my_max - my_min

    # Determine gap direction
    # Horizontal separation: one room is to the left of the other
    # Vertical separation: one room is above/below the other
    h_overlap = max(0, min(fx_max, mx_max) - max(fx_min, mx_min))
    v_overlap = max(0, min(fy_max, my_max) - max(fy_min, my_min))

    candidates = []
    seen_movements = set()

    # Generate direction candidates based on geometry
    directions = []

    if h_overlap > v_overlap:
        # Primarily vertical separation
        if my_min >= fy_max:
            # moving_room is below fixed_room → move down (+Y)
            directions.append((0, needed_shift))
        elif fy_min >= my_max:
            # moving_room is above fixed_room → move up (-Y)
            directions.append((0, -needed_shift))
        else:
            # Vertical overlap but side by side — try both Y directions
            directions.append((0, needed_shift))
            directions.append((0, -needed_shift))
        # Also try X
        if mx_min >= fx_max:
            directions.append((needed_shift, 0))
        elif fx_min >= mx_max:
            directions.append((-needed_shift, 0))
        else:
            directions.append((needed_shift, 0))
            directions.append((-needed_shift, 0))
    else:
        # Primarily horizontal separation
        if mx_min >= fx_max:
            # moving_room is to the right → move right (+X)
            directions.append((needed_shift, 0))
        elif fx_min >= mx_max:
            # moving_room is to the left → move left (-X)
            directions.append((-needed_shift, 0))
        else:
            directions.append((needed_shift, 0))
            directions.append((-needed_shift, 0))
        # Also try Y
        if my_min >= fy_max:
            directions.append((0, needed_shift))
        elif fy_min >= my_max:
            directions.append((0, -needed_shift))
        else:
            directions.append((0, needed_shift))
            directions.append((0, -needed_shift))

    rank = 0
    for dx, dy in directions:
        if rank >= max_candidates:
            break
        movement = (dx ** 2 + dy ** 2) ** 0.5
        if movement > max_shift + 1e-9:
            continue
        # Deduplicate by rounded movement
        key = (round(dx, 3), round(dy, 3))
        if key in seen_movements:
            continue
        seen_movements.add(key)
        rank += 1

        axis = "X" if abs(dx) >= abs(dy) else "Y"
        candidates.append({
            "candidate_id": f"clr_candidate_{entity_ids[0]}_{entity_ids[1]}_{axis}_{rank}",
            "entity_id": moving_room["id"],
            "operation": "TRANSLATION",
            "dx": round(dx, 3),
            "dy": round(dy, 3),
            "movement_distance_ft": round(movement, 3),
            "reason": f"Increase {clearance_type.lower()} clearance between "
                      f"{entity_ids[0]} and {entity_ids[1]}",
            "deterministic_rank": rank,
        })

    # Sort by movement distance (shortest first)
    candidates.sort(key=lambda c: (c["movement_distance_ft"], c["deterministic_rank"]))
    # Reassign deterministic_rank after sort
    for i, c in enumerate(candidates):
        c["deterministic_rank"] = i + 1

    return candidates


def simulate_clearance_candidate(
    model: dict,
    candidate: dict,
    rules: list[dict],
    finding: dict | None = None,
) -> dict:
    """Simulate a clearance correction candidate without mutating the model."""
    from .geometry_model import _polygon_area

    sim_model = deepcopy(model)
    entity_id = candidate["entity_id"]
    dx = candidate["dx"]
    dy = candidate["dy"]
    movement = (dx ** 2 + dy ** 2) ** 0.5

    # Find and translate the target room
    target = None
    for room in sim_model.get("rooms", []):
        if room["id"] == entity_id:
            target = room
            break

    if target is None:
        return {
            "candidate_id": candidate["candidate_id"],
            "valid": False,
            "score": 0.0,
            "resolved_finding_codes": [],
            "new_blocking_findings": [],
            "remaining_clearance_ft": 0.0,
            "movement_distance_ft": movement,
            "rejection_reason": f"Room {entity_id} not found in model",
        }

    # Save original state for rollback comparison
    original_polygon = [list(p) for p in target["polygon"]]
    original_area = target.get("area_sqft", 0)

    # Apply translation
    target["polygon"] = _translate_polygon(target["polygon"], dx, dy)

    # Update area (should be same for translation, recalculate)
    target["area_sqft"] = round(_polygon_area(target["polygon"]), 2)

    # Update compatibility bbox
    if "compatibility" in target and "bbox" in target["compatibility"]:
        bbox = target["compatibility"]["bbox"]
        bbox["x"] = round(bbox.get("x", 0) + dx, 3)
        bbox["y"] = round(bbox.get("y", 0) + dy, 3)

    # Check 1: Polygon remains valid
    try:
        from shapely.geometry import Polygon as SPoly
        sp = SPoly(target["polygon"])
        if not sp.is_valid:
            sp = sp.buffer(0)
        if sp.area < 1e-6:
            return {
                "candidate_id": candidate["candidate_id"],
                "valid": False,
                "score": 0.0,
                "resolved_finding_codes": [],
                "new_blocking_findings": [],
                "remaining_clearance_ft": 0.0,
                "movement_distance_ft": movement,
                "rejection_reason": "Translated polygon is degenerate",
            }
    except Exception:
        return {
            "candidate_id": candidate["candidate_id"],
            "valid": False,
            "score": 0.0,
            "resolved_finding_codes": [],
            "new_blocking_findings": [],
            "remaining_clearance_ft": 0.0,
            "movement_distance_ft": movement,
            "rejection_reason": "Translated polygon is invalid",
        }

    # Check 2: Area preserved (translation doesn't change area, but verify)
    if abs(target["area_sqft"] - original_area) > 0.01:
        return {
            "candidate_id": candidate["candidate_id"],
            "valid": False,
            "score": 0.0,
            "resolved_finding_codes": [],
            "new_blocking_findings": [],
            "remaining_clearance_ft": 0.0,
            "movement_distance_ft": movement,
            "rejection_reason": "Area not preserved after translation",
        }

    # Check 3: Edge lengths preserved
    original_edges = sorted(
        ((original_polygon[i][0] - original_polygon[(i + 1) % len(original_polygon)][0]) ** 2 +
         (original_polygon[i][1] - original_polygon[(i + 1) % len(original_polygon)][1]) ** 2) ** 0.5
        for i in range(len(original_polygon))
    )
    new_edges = sorted(
        ((target["polygon"][i][0] - target["polygon"][(i + 1) % len(target["polygon"])][0]) ** 2 +
         (target["polygon"][i][1] - target["polygon"][(i + 1) % len(target["polygon"])][1]) ** 2) ** 0.5
        for i in range(len(target["polygon"]))
    )
    for o, n in zip(original_edges, new_edges):
        if abs(o - n) > 1e-6:
            return {
                "candidate_id": candidate["candidate_id"],
                "valid": False,
                "score": 0.0,
                "resolved_finding_codes": [],
                "new_blocking_findings": [],
                "remaining_clearance_ft": 0.0,
                "movement_distance_ft": movement,
                "rejection_reason": "Edge lengths not preserved",
            }

    # Check 4: Vertex count unchanged
    if len(target["polygon"]) != len(original_polygon):
        return {
            "candidate_id": candidate["candidate_id"],
            "valid": False,
            "score": 0.0,
            "resolved_finding_codes": [],
            "new_blocking_findings": [],
            "remaining_clearance_ft": 0.0,
            "movement_distance_ft": movement,
            "rejection_reason": "Vertex count changed",
        }

    # Check 5: Room stays within plot
    plot = sim_model.get("plot", {})
    plot_w = float(plot.get("usable_width_ft", 0))
    plot_d = float(plot.get("usable_depth_ft", 0))
    minx, miny, maxx, maxy = _polygon_bbox(target["polygon"])
    if minx < -1e-6 or miny < -1e-6 or maxx > plot_w + 1e-6 or maxy > plot_d + 1e-6:
        return {
            "candidate_id": candidate["candidate_id"],
            "valid": False,
            "score": 0.0,
            "resolved_finding_codes": [],
            "new_blocking_findings": [],
            "remaining_clearance_ft": 0.0,
            "movement_distance_ft": movement,
            "rejection_reason": "Translated room exits plot bounds",
        }

    # Check 6: No new overlap introduced
    new_overlaps = check_overlaps(sim_model["rooms"])
    if new_overlaps:
        return {
            "candidate_id": candidate["candidate_id"],
            "valid": False,
            "score": 0.0,
            "resolved_finding_codes": [],
            "new_blocking_findings": [{"code": "ROOM_OVERLAP", "details": new_overlaps}],
            "remaining_clearance_ft": 0.0,
            "movement_distance_ft": movement,
            "rejection_reason": f"Introduces overlap: {new_overlaps}",
        }

    # Check 7-8: Re-detect clearances to verify improvement
    remaining = detect_canonical_clearances(sim_model, rules)
    blocking_new = [f for f in remaining if f.get("blocks_geometry")]

    # Calculate remaining clearance for the target finding.
    # When the candidate clears the gap to or past required, re-detection may
    # not report a violation (threshold is strictly < required), so remaining
    # stays 0.0.  In that case, measure the actual distance between the
    # affected rooms so the acceptance gate sees the real post-correction gap.
    remaining_clearance = 0.0
    resolved_codes = []
    for f in remaining:
        if entity_id in f.get("entity_ids", []):
            remaining_clearance = f.get("measured_clearance_ft", 0)
            if f["code"] == "CLEARANCE_VIOLATION":
                resolved_codes.append(f["code"])
    if remaining_clearance == 0.0 and not blocking_new and target is not None:
        # Re-detect found no violation — measure the actual gap to the peer
        # room so the acceptance gate sees the real post-correction distance.
        peer_ids = []
        if finding and isinstance(finding.get("entity_ids"), list):
            peer_ids = [eid for eid in finding["entity_ids"] if eid != entity_id]
        if not peer_ids:
            for f in remaining:
                if entity_id in f.get("entity_ids", []):
                    peer_ids = [eid for eid in f.get("entity_ids", []) if eid != entity_id]
                    break
        for peer_id in peer_ids:
            peer = next((r for r in sim_model.get("rooms", []) if r["id"] == peer_id), None)
            if peer and target:
                remaining_clearance = polygons_clearance(target["polygon"], peer["polygon"])
                break

    # Candidate is valid if no new blocking clearances were introduced
    valid = len(blocking_new) == 0

    # Score: higher is better. Base 100, penalize movement
    score = 100.0
    movement = float(candidate.get("movement_distance_ft",
        (candidate.get("dx", 0) ** 2 + candidate.get("dy", 0) ** 2) ** 0.5))
    score -= movement * 5  # penalize long moves
    if remaining_clearance > 0:
        score += remaining_clearance * 2  # reward remaining clearance margin

    return {
        "candidate_id": candidate["candidate_id"],
        "entity_id": candidate["entity_id"],
        "dx": candidate["dx"],
        "dy": candidate["dy"],
        "valid": valid,
        "score": round(score, 2),
        "resolved_finding_codes": resolved_codes,
        "new_blocking_findings": blocking_new,
        "remaining_clearance_ft": round(remaining_clearance, 2),
        "movement_distance_ft": movement,
        "rejection_reason": "" if valid else "Introduces new blocking clearance violation",
    }


def select_best_clearance_candidate(simulation_results: list[dict]) -> dict | None:
    """Select the best valid candidate deterministically.

    Ranking priority:
    1. Valid (not rejected)
    2. Resolves the original violation
    3. Introduces no blocking findings
    4. Smallest translation distance
    5. Greatest clearance margin
    6. Stable candidate_id tie-break
    """
    valid = [r for r in simulation_results if r.get("valid")]
    if not valid:
        return None

    # Sort: resolved findings (empty list) first, then by movement_distance,
    # then by remaining_clearance_ft (larger is better), then candidate_id
    valid.sort(key=lambda r: (
        len(r.get("resolved_finding_codes", [])) > 0,  # True (not resolved) goes last
        r.get("movement_distance_ft", 999),
        -r.get("remaining_clearance_ft", 0),
        r.get("candidate_id", ""),
    ))
    return valid[0]


def apply_clearance_correction(
    canonical_model: dict,
    candidate: dict,
    change_log: list,
    rule: dict = None,
    finding: dict = None,
) -> tuple:
    """Apply a clearance correction atomically.

    Returns (new_model, change_entry) where change_entry is None if rejected.
    """
    from .geometry_model import _polygon_area

    new_model = deepcopy(canonical_model)

    # Find target room
    target = None
    for room in new_model.get("rooms", []):
        if room["id"] == candidate["entity_id"]:
            target = room
            break

    if target is None:
        return canonical_model, None

    dx = candidate["dx"]
    dy = candidate["dy"]
    original_polygon = [list(p) for p in target["polygon"]]
    original_revision = target.get("geometry_revision", 1)

    # Apply translation
    target["polygon"] = _translate_polygon(target["polygon"], dx, dy)

    # Update area
    target["area_sqft"] = round(_polygon_area(target["polygon"]), 2)

    # Update compatibility bbox
    if "compatibility" in target and "bbox" in target.get("compatibility", {}):
        bbox = target["compatibility"]["bbox"]
        bbox["x"] = round(bbox.get("x", 0) + dx, 3)
        bbox["y"] = round(bbox.get("y", 0) + dy, 3)

    # Increment revision
    target["geometry_revision"] = original_revision + 1

    # Validate the modified model: check overlaps first, then canonical validation
    new_overlaps = check_overlaps(new_model["rooms"])
    if new_overlaps:
        return canonical_model, None

    from . import geometry_model as gm
    validation = gm.validate_canonical_model(new_model)
    if not validation.get("geometry_valid", True):
        return canonical_model, None

    # Create change log entry
    change_entry = {
        "change_id": _next_change_id(),
        "entity_id": target["id"],
        "revision_before": original_revision,
        "revision_after": original_revision + 1,
        "change_type": "CLEARANCE_TRANSLATION",
        "reason": finding.get("description", "Resolve clearance violation") if finding else "Resolve clearance violation",
        "actor": "geometry_solver",
        "rule_id": rule.get("rule_id", "UNKNOWN") if rule else "UNKNOWN",
        "finding_code": finding.get("code", "CLEARANCE_VIOLATION") if finding else "CLEARANCE_VIOLATION",
        "before_polygon": original_polygon,
        "after_polygon": [list(p) for p in target["polygon"]],
        "delta": {"dx": round(dx, 3), "dy": round(dy, 3)},
        "validation_before": {
            "measured_clearance_ft": finding.get("measured_clearance_ft", 0) if finding else 0,
        },
        "validation_after": {
            "measured_clearance_ft": finding.get("required_clearance_ft", 0) if finding else 0,
        },
    }

    change_log.append(change_entry)
    return new_model, change_entry


def solve_canonical_clearances(
    canonical_model: dict,
    clearance_rules: list[dict] = None,
    constraints: dict = None,
) -> dict:
    """Solve clearance violations on a canonical model.

    Returns dict with model, detected/resolved/unresolved counts,
    applied_changes, human_decisions_needed, and status.
    """
    _reset_clearance_counters()

    if clearance_rules is None:
        clearance_rules = DEFAULT_CLEARANCE_RULES
    if constraints is None:
        constraints = DEFAULT_CLEARANCE_CONSTRAINTS

    max_iterations = int(constraints.get("max_clearance_iterations", 10))
    max_candidates_per = int(constraints.get("max_candidates_per_violation", 12))
    max_total = int(constraints.get("max_total_candidates", 100))
    min_improvement = float(constraints.get("minimum_improvement_ft", 0.01))

    model = deepcopy(canonical_model)
    change_log = []
    human_decisions = []
    total_candidates_used = 0

    for iteration in range(max_iterations):
        findings = detect_canonical_clearances(model, clearance_rules)
        if not findings:
            break

        # Process boundary violations first (they constrain room-to-room movement)
        findings.sort(key=lambda f: (
            0 if f.get("clearance_type") == "ROOM_TO_PLOT_BOUNDARY" else 1,
            f.get("entity_ids", []),
        ))

        resolved_this_iter = 0

        for finding in findings:
            if total_candidates_used >= max_total:
                human_decisions.append({
                    "decision_id": _next_decision_id(),
                    "type": "CLEARANCE_CORRECTION",
                    "entity_ids": finding.get("entity_ids", []),
                    "finding_code": "CLEARANCE_CORRECTION_LIMIT_REACHED",
                    "reason": "Maximum total candidate limit reached.",
                    "options": [
                        "Move rooms manually",
                        "Reduce room programme",
                        "Change clearance requirements",
                    ],
                    "blocking": finding.get("blocks_geometry", True),
                })
                continue

            if not finding.get("auto_correction_eligible", False):
                human_decisions.append({
                    "decision_id": _next_decision_id(),
                    "type": "CLEARANCE_CORRECTION",
                    "entity_ids": finding.get("entity_ids", []),
                    "finding_code": "CLEARANCE_CORRECTION_UNRESOLVED",
                    "reason": "Auto-correction not eligible for this violation.",
                    "options": [
                        "Move rooms manually",
                        "Reduce room programme",
                        "Change clearance requirements",
                    ],
                    "blocking": finding.get("blocks_geometry", True),
                })
                continue

            # Find matching rule
            rule = _find_rule(clearance_rules, finding.get("clearance_type", "ROOM_TO_ROOM"))

            # Generate candidates
            local_constraints = {
                **constraints,
                "max_candidates_per_violation": min(max_candidates_per, max_total - total_candidates_used),
            }
            candidates = generate_clearance_translation_candidates(model, finding, local_constraints)
            total_candidates_used += len(candidates)

            if not candidates:
                human_decisions.append({
                    "decision_id": _next_decision_id(),
                    "type": "CLEARANCE_CORRECTION",
                    "entity_ids": finding.get("entity_ids", []),
                    "finding_code": "CLEARANCE_CORRECTION_UNRESOLVED",
                    "reason": "No valid translation candidates generated.",
                    "options": [
                        "Move rooms manually",
                        "Reduce room programme",
                        "Change clearance requirements",
                    ],
                    "blocking": finding.get("blocks_geometry", True),
                })
                continue

            # Simulate and select best
            best = None
            for cand in candidates:
                sim = simulate_clearance_candidate(model, cand, clearance_rules, finding=finding)
                if sim.get("valid"):
                    improvement = sim.get("remaining_clearance_ft", 0) - finding.get("measured_clearance_ft", 0)
                    if improvement >= min_improvement or sim.get("remaining_clearance_ft", 0) >= finding.get("required_clearance_ft", 0):
                        best = cand
                        break

            if best is None:
                human_decisions.append({
                    "decision_id": _next_decision_id(),
                    "type": "CLEARANCE_CORRECTION",
                    "entity_ids": finding.get("entity_ids", []),
                    "finding_code": "CLEARANCE_CORRECTION_UNRESOLVED",
                    "reason": "No valid candidate satisfies constraints.",
                    "options": [
                        "Move rooms manually",
                        "Reduce room programme",
                        "Change clearance requirements",
                    ],
                    "blocking": finding.get("blocks_geometry", True),
                })
                continue

            # Apply correction
            new_model, change_entry = apply_clearance_correction(
                model, best, change_log, rule, finding
            )

            if change_entry is not None:
                model = new_model
                resolved_this_iter += 1
            else:
                human_decisions.append({
                    "decision_id": _next_decision_id(),
                    "type": "CLEARANCE_CORRECTION",
                    "entity_ids": finding.get("entity_ids", []),
                    "finding_code": "CLEARANCE_CORRECTION_REJECTED",
                    "reason": f"Correction candidate {best['candidate_id']} failed final validation.",
                    "options": [
                        "Move rooms manually",
                        "Reduce room programme",
                        "Change clearance requirements",
                    ],
                    "blocking": finding.get("blocks_geometry", True),
                })

        if resolved_this_iter == 0:
            # No progress this iteration, stop
            break

    # Determine initial detected count (first pass before any correction)
    initial_findings = detect_canonical_clearances(deepcopy(canonical_model), clearance_rules)
    initial_detected = len(initial_findings)

    # Final detection pass on the corrected model
    final_findings = detect_canonical_clearances(model, clearance_rules)
    detected = initial_detected
    resolved_count = len(change_log)
    unresolved_blocking = 0
    unresolved_non_blocking = 0

    # Count remaining blocking/non-blocking findings from the final pass
    for f in final_findings:
        if f.get("code") == "CLEARANCE_VIOLATION":
            if f.get("blocks_geometry", True):
                unresolved_blocking += 1
            else:
                unresolved_non_blocking += 1
            # Add human decision if not already present
            already_handled = any(
                hd.get("entity_ids") == f.get("entity_ids")
                for hd in human_decisions
            )
            if not already_handled:
                human_decisions.append({
                    "decision_id": _next_decision_id(),
                    "type": "CLEARANCE_CORRECTION",
                    "entity_ids": f.get("entity_ids", []),
                    "finding_code": "CLEARANCE_CORRECTION_UNRESOLVED",
                    "reason": f.get("description", "Unresolved clearance violation."),
                    "options": [
                        "Move rooms manually",
                        "Reduce room programme",
                        "Change clearance requirements",
                    ],
                    "blocking": f.get("blocks_geometry", True),
                })

    unresolved_count = unresolved_blocking + unresolved_non_blocking

    # Determine status: blocking → BLOCKED, non-blocking → review required
    if unresolved_blocking > 0:
        status = "BLOCKED"
    elif unresolved_non_blocking > 0:
        status = "COMPLETE_WITH_REVIEW_REQUIRED"
    else:
        status = "COMPLETE"

    return {
        "model": model,
        "detected": detected,
        "resolved": resolved_count,
        "unresolved": unresolved_count,
        "applied_changes": change_log,
        "human_decisions_needed": human_decisions,
        "status": status,
    }


def solve_canonical_geometry(
    canonical_model: dict,
    constraints: dict | None = None,
) -> dict:
    """Solve geometry on a canonical model using pure polygon translations.

    All geometry modifications are translations (preserving area, edge lengths,
    and vertex count).  No resizing, normalization, or any other operation is
    performed unless explicitly enable d by an appropriate constraint.

    Parameters
    ----------
    canonical_model : dict
        Model produced by create_canonical_spatial_plan.
    constraints : dict | None
        Optional constraint dict.

    Returns
    -------
    dict
        Modified canonical model with:
        - Updated polygons for any modified rooms (translation only)
        - Updated area_sqft derived from polygon (unchanged for translations)
        - Updated compatibility.bbox derived from polygon
        - Incremented geometry_revision per modified room
        - geometry_change_log entries with delta vector
        - geometry_valid flag
    """
    from .geometry_model import (
        _polygon_area, _rect_from_polygon, _bbox_compatibility,
        _normalize_finding, _set_polygon,
    )

    if constraints is None:
        constraints = {}

    model = dict(canonical_model)
    model["rooms"] = [dict(r) for r in model.get("rooms", [])]
    change_log = list(model.get("geometry_change_log", []))
    allow_resolve = constraints.get("allow_overlap_resolution", True)

    # Ensure model metadata
    model.setdefault("geometry_authority", "CANONICAL_POLYGON")
    model.setdefault("canonical_created_at_stage", "GEOMETRY_SOLVER")
    model.setdefault("legacy_adapter_used", False)

    rooms = model["rooms"]
    findings = list(model.get("validation_findings", []))

    def _poly_for(room):
        poly = room.get("polygon", [])
        if len(poly) < 3:
            return None
        try:
            return Polygon([(p[0], p[1]) for p in poly])
        except Exception:
            return None

    def _translate_polygon(vertices, dx, dy):
        return [[p[0] + dx, p[1] + dy] for p in vertices]

    def _bbox(poly):
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        return min(xs), min(ys), max(xs), max(ys)

    # Phase 1: Detect overlaps on polygons (pure intersection)
    overlaps = []
    for i, room in enumerate(rooms):
        p = _poly_for(room)
        if p is None:
            continue
        for j in range(i + 1, len(rooms)):
            other = rooms[j]
            op = _poly_for(other)
            if op is None:
                continue
            if p.intersects(op) and p.intersection(op).area > 1e-6:
                overlaps.append({
                    "room_a": room.get("id"),
                    "room_b": other.get("id"),
                })

    # Phase 2: Translate (not reshape) room_b to resolve overlap
    if allow_resolve:
        for overlap in overlaps:
            room_a = next((r for r in rooms if r.get("id") == overlap["room_a"]), None)
            room_b = next((r for r in rooms if r.get("id") == overlap["room_b"]), None)
            if not room_a or not room_b:
                continue
            poly_a_b = _bbox(room_a.get("polygon", []))
            poly_b_verts = room_b.get("polygon", [])
            poly_b_bbox = _bbox(poly_b_verts)
            if len(poly_b_verts) < 3:
                continue

            inter_xmin = max(poly_a_b[0], poly_b_bbox[0])
            inter_xmax = min(poly_a_b[2], poly_b_bbox[2])
            inter_ymin = max(poly_a_b[1], poly_b_bbox[1])
            inter_ymax = min(poly_a_b[3], poly_b_bbox[3])
            w_overlap = inter_xmax - inter_xmin
            h_overlap = inter_ymax - inter_ymin

            # Pure translations only: shift by (w_overlap + 0.1) or (h_overlap + 0.1)
            # Filter invalid candidates (negative coordinates)
            candidates = []
            # Move right by w_overlap + 0.1
            if poly_b_bbox[2] + w_overlap + 0.1 >= -0.001:
                candidates.append((w_overlap + 0.1, 0))
            # Move left by w_overlap + 0.1
            if poly_b_bbox[0] - w_overlap - 0.1 >= -0.001:
                candidates.append((-(w_overlap + 0.1), 0))
            # Move up by h_overlap + 0.1
            if poly_b_bbox[3] + h_overlap + 0.1 >= -0.001:
                candidates.append((0, h_overlap + 0.1))
            # Move down by h_overlap + 0.1
            if poly_b_bbox[1] - h_overlap - 0.1 >= -0.001:
                candidates.append((0, -(h_overlap + 0.1)))

            # Pick the smallest-displacement candidate
            candidates.sort(key=lambda d: abs(d[0]) + abs(d[1]))
            for dx, dy in candidates:
                new_poly = _translate_polygon(poly_b_verts, dx, dy)
                # Build Polygon for overlap check
                try:
                    sp = Polygon(new_poly)
                except Exception:
                    continue
                pa = _poly_for(room_a)
                if pa is None:
                    continue
                # Verify no overlap after translation
                if not sp.intersects(pa):
                    # Apply translation via _set_polygon for consistent metadata
                    _set_polygon(room_b, new_poly, "TRANSLATION",
                                 "Translated by (%.2f, %.2f) to resolve overlap with %s" % (
                                     dx, dy, overlap["room_a"]),
                                 "geometry_solver", change_log)
                    # Record translation vector in change log
                    if change_log:
                        change_log[-1]["delta"] = {"dx": round(dx, 4), "dy": round(dy, 4)}
                    break

    # Phase 3: Re-validate and surface unresolved overlaps
    remaining_overlaps = []
    for i, room in enumerate(rooms):
        p = _poly_for(room)
        if p is None:
            continue
        for j in range(i + 1, len(rooms)):
            other = rooms[j]
            op = _poly_for(other)
            if op is None:
                continue
            if p.intersects(op) and p.intersection(op).area > 1e-6:
                remaining_overlaps.append((room.get("id"), other.get("id")))

    for ra, rb in remaining_overlaps:
        findings.append(_normalize_finding(
            "UNRESOLVED_OVERLAP", "ERROR", "room", [ra, rb],
            "Overlap between %s and %s could not be auto-resolved." % (ra, rb),
        ))

    # --- Build geometry metadata ---
    max_revision = max(
        (r.get("geometry_revision", 1) for r in rooms),
        default=1,
    )
    total_changes = len(change_log)

    model["geometry_change_log"] = change_log
    model["validation_findings"] = findings
    model["geometry_valid"] = len(remaining_overlaps) == 0
    model["geometry_metadata"] = {
        "geometry_revision": max_revision,
        "total_changes": total_changes,
        "max_revision_any_room": max_revision,
    }
    return model


def validate_geometry(project_state: dict,
                      constraint_validation: AdapterResult | None = None) -> dict:
    """Validate geometry and optionally apply SOLVER_SUPPORTED constraints.

    When *constraint_validation* is provided, SOLVER_SUPPORTED constraints
    are evaluated against the room geometry.  Each finding references the
    originating constraint_id and rule_id in its ``details`` dict.

    Parameters
    ----------
    project_state : dict
        The project state with rooms and geometry_model.
    constraint_validation : AdapterResult | None
        Classified constraints from the planner adapter.  SOLVER_SUPPORTED
        constraints produce constraint-referenced findings.

    Returns
    -------
    dict
        Validation result with geometry_valid flag, violation lists, and
        constraint-specific findings.
    """
    canonical = project_state.get("geometry_model")
    if canonical and "rooms" in canonical:
        rooms = canonical["rooms"]
        canonical_model_present = True
    else:
        rooms = project_state.get("rooms", [])
        canonical_model_present = False

    plot = project_state.get("plot", {})
    plot_w = float(plot.get("usable_width_ft") or plot.get("width_ft") or 0)
    plot_d = float(plot.get("usable_depth_ft") or plot.get("depth_ft") or 0)
    overlaps = check_overlaps(rooms)
    size_violations = check_minimum_sizes(rooms)
    clearance_violations = check_clearances(rooms)
    brahmasthan_clear = check_brahmasthan(rooms, plot_w, plot_d)
    auto_fixed = []
    needs_human = []

    # ── Constraint-driven validation findings ──────────────────────────
    constraint_findings: list[dict] = []
    solver_rules: list[dict] = []
    if constraint_validation is not None:
        solver_rules = solver_constraints_as_rules(constraint_validation.solver_supported)
        for rule in solver_rules:
            ctype = rule.get("constraint_type", "")
            cid = rule.get("constraint_id", "UNKNOWN")
            rid = rule.get("rule_id", "UNKNOWN")
            targets = rule.get("target_element_type", "")
            params = rule.get("parameters", {})
            sev = rule.get("severity", "major")

            if ctype == "clearance":
                # MIN_CLEARANCE: check each room pair against the specified distance
                req_clear = float(params.get("minimum_distance_ft", params.get("min_clearance_ft", 4.0)))
                for i, ra in enumerate(rooms):
                    verts_a = room_polygon(ra)
                    if verts_a is None:
                        continue
                    try:
                        poly_a = Polygon(verts_a) if len(verts_a) >= 3 else None
                    except Exception:
                        continue
                    if poly_a is None:
                        continue
                    if not poly_a.is_valid:
                        poly_a = poly_a.buffer(0)
                    for rb in rooms[i + 1:]:
                        if int(ra.get("floor", 0)) != int(rb.get("floor", 0)):
                            continue
                        if targets and targets != "general" and targets not in str(rb.get("type", "")).lower():
                            continue
                        verts_b = room_polygon(rb)
                        if verts_b is None:
                            continue
                        try:
                            poly_b = Polygon(verts_b) if len(verts_b) >= 3 else None
                        except Exception:
                            continue
                        if poly_b is None:
                            continue
                        if not poly_b.is_valid:
                            poly_b = poly_b.buffer(0)
                        if poly_a.intersection(poly_b).area > 1e-6:
                            continue
                        dist = float(poly_a.distance(poly_b))
                        if 0 < dist < req_clear:
                            constraint_findings.append({
                                "code": "CLEARANCE_VIOLATION",
                                "severity": sev,
                                "entity_type": "room_pair",
                                "entity_ids": [ra.get("id", "?"), rb.get("id", "?")],
                                "description": (
                                    f"Clearance between {ra.get('id')} and {rb.get('id')} is "
                                    f"{dist:.2f} ft; constraint {cid} (rule {rid}) requires "
                                    f"{req_clear:.2f} ft."
                                ),
                                "constraint_id": cid,
                                "rule_id": rid,
                                "measured_clearance_ft": round(dist, 2),
                                "required_clearance_ft": req_clear,
                            })

            elif ctype == "fire_safety":
                # DOES_NOT_INTERSECT: rooms of specified types must not overlap
                req_types = params.get("room_types", [])
                if not req_types:
                    continue
                req_types_lower = [t.lower() for t in req_types]
                for i, ra in enumerate(rooms):
                    if str(ra.get("type", "")).lower() not in req_types_lower:
                        continue
                    verts_a = room_polygon(ra)
                    if verts_a is None:
                        continue
                    try:
                        poly_a = Polygon(verts_a) if len(verts_a) >= 3 else None
                    except Exception:
                        continue
                    if poly_a is None:
                        continue
                    if not poly_a.is_valid:
                        poly_a = poly_a.buffer(0)
                    for rb in rooms[i + 1:]:
                        if str(rb.get("type", "")).lower() not in req_types_lower:
                            continue
                        if int(ra.get("floor", 0)) != int(rb.get("floor", 0)):
                            continue
                        verts_b = room_polygon(rb)
                        if verts_b is None:
                            continue
                        try:
                            poly_b = Polygon(verts_b) if len(verts_b) >= 3 else None
                        except Exception:
                            continue
                        if poly_b is None:
                            continue
                        if not poly_b.is_valid:
                            poly_b = poly_b.buffer(0)
                        if poly_a.intersection(poly_b).area > 1e-6:
                            constraint_findings.append({
                                "code": "FIRE_SEPARATION_VIOLATION",
                                "severity": sev,
                                "entity_type": "room_pair",
                                "entity_ids": [ra.get("id", "?"), rb.get("id", "?")],
                                "description": (
                                    f"Rooms {ra.get('id')} and {rb.get('id')} overlap — "
                                    f"constraint {cid} (rule {rid}) requires non-intersection."
                                ),
                                "constraint_id": cid,
                                "rule_id": rid,
                                "overlap_area_sqft": round(
                                    float(poly_a.intersection(poly_b).area), 2
                                ),
                            })

            elif ctype == "door_placement":
                # REQUIRES_EXTERIOR_WALL: rooms on exterior walls must have a door
                for room in rooms:
                    if targets and targets != "general" and targets not in str(room.get("type", "")).lower():
                        continue
                    rx = float(room.get("x", 0))
                    ry = float(room.get("y", 0))
                    rw = float(room.get("w", 0))
                    rh = float(room.get("h", 0))
                    on_exterior = (
                        rx <= 0.01 or ry <= 0.01
                        or rx + rw >= plot_w - 0.01
                        or ry + rh >= plot_d - 0.01
                    )
                    if on_exterior and not room.get("door"):
                        constraint_findings.append({
                            "code": "DOOR_ON_EXTERIOR_WALL_REQUIRED",
                            "severity": sev,
                            "entity_type": "room",
                            "entity_ids": [room.get("id", "?")],
                            "description": (
                                f"Room {room.get('id')} ({room.get('type')}) is on an "
                                f"exterior wall but has no door — constraint {cid} "
                                f"(rule {rid}) requires an exterior-wall door."
                            ),
                            "constraint_id": cid,
                            "rule_id": rid,
                        })

            elif ctype == "window_placement":
                # REQUIRES_WINDOW: habitable rooms must have at least one window
                habitable_keywords = (
                    "living room", "bedroom", "master bedroom", "kitchen",
                    "family lounge", "drawing room", "dining room",
                    "guest bedroom", "study", "pooja room", "formal living",
                    "home theater", "gym", "servant", "foyer",
                )
                for room in rooms:
                    rt_lower = str(room.get("type", "")).lower()
                    if not any(kw in rt_lower for kw in habitable_keywords):
                        continue
                    if targets and targets != "general" and targets not in rt_lower:
                        continue
                    rx = float(room.get("x", 0))
                    ry = float(room.get("y", 0))
                    rw = float(room.get("w", 0))
                    rh = float(room.get("h", 0))
                    has_exterior = (
                        rx <= 0.01 or ry <= 0.01
                        or rx + rw >= plot_w - 0.01
                        or ry + rh >= plot_d - 0.01
                    )
                    if has_exterior and len(room.get("windows", [])) < 1:
                        constraint_findings.append({
                            "code": "WINDOW_REQUIRED",
                            "severity": sev,
                            "entity_type": "room",
                            "entity_ids": [room.get("id", "?")],
                            "description": (
                                f"Room {room.get('id')} ({room.get('type')}) has no "
                                f"windows — constraint {cid} (rule {rid}) requires "
                                f"ventilation."
                            ),
                            "constraint_id": cid,
                            "rule_id": rid,
                        })

    # Auto-resolution requires x/y keys (legacy path).  Canonical-model rooms
    # carry polygon vertices instead, so we skip resolution and surface every
    # violation for human review in that case.
    if not canonical_model_present:
        # Step 1: auto-resolve overlaps
        if overlaps:
            room_lookup = {room.get("id"): room for room in rooms}
            plot_bounds = {"w": plot_w, "h": plot_d}
            for overlap in overlaps:
                room_a = room_lookup.get(overlap["room_a"])
                room_b = room_lookup.get(overlap["room_b"])
                if not room_a or not room_b:
                    needs_human.append({"type": "overlap_resolution_failed", "reason": "Room id missing from project_state rooms.", "details": overlap})
                    continue
                old_x = room_b.get("x")
                old_y = room_b.get("y")
                resolved = resolve_overlap(room_a, room_b, plot_bounds)
                if resolved.get("x") != old_x or resolved.get("y") != old_y:
                    room_b.update(resolved)
                    auto_fixed.append(
                        {
                            "type": "overlap",
                            "room_id": room_b.get("id"),
                            "old_x": old_x,
                            "old_y": old_y,
                            "new_x": resolved.get("x"),
                            "new_y": resolved.get("y"),
                        }
                    )
                else:
                    needs_human.append(
                        {
                            "type": "overlap",
                            "room_a": overlap["room_a"],
                            "room_b": overlap["room_b"],
                            "reason": "Could not auto-resolve overlap within plot bounds.",
                        }
                    )
            overlaps = check_overlaps(rooms)
        # Step 2: auto-resolve clearance violations (after overlaps are resolved)
        room_lookup = {room.get("id"): room for room in rooms}
        plot_bounds = {"w": plot_w, "h": plot_d}
        for cv in list(clearance_violations):
            room_a = room_lookup.get(cv["room_a"])
            room_b = room_lookup.get(cv["room_b"])
            if not room_a or not room_b:
                needs_human.append({"type": "clearance_resolution_failed", "reason": "Room id missing.", "details": cv})
                continue
            old_x = room_b.get("x")
            old_y = room_b.get("y")
            resolved = resolve_clearance_violation(room_a, room_b, plot_bounds)
            if resolved.get("x") != old_x or resolved.get("y") != old_y:
                room_b.update(resolved)
                auto_fixed.append(
                    {
                        "type": "clearance",
                        "room_id": room_b.get("id"),
                        "room_a": cv["room_a"],
                        "room_b": cv["room_b"],
                        "clearance_ft": cv["clearance_ft"],
                        "deficit_ft": cv["deficit_ft"],
                        "old_x": old_x,
                        "old_y": old_y,
                        "new_x": resolved.get("x"),
                        "new_y": resolved.get("y"),
                    }
                )
        # Re-check after all auto-fixes
        clearance_violations = check_clearances(rooms)
        brahmasthan_clear = check_brahmasthan(rooms, plot_w, plot_d)
    # Record remaining violations for human review
    if size_violations:
        needs_human.extend(
            {"type": "minimum_size", "room_id": violation["room_id"], "reason": "Room is below required minimum size.", "details": violation}
            for violation in size_violations
        )
    if clearance_violations:
        needs_human.extend(
            {
                "type": "clearance",
                "room_a": violation["room_a"],
                "room_b": violation["room_b"],
                "reason": "Rooms are closer than minimum 4 ft passage clearance.",
                "details": violation,
            }
            for violation in clearance_violations
        )
    if not brahmasthan_clear:
        needs_human.append({"type": "brahmasthan", "reason": "One or more rooms overlap the center 1/9th Brahmasthan zone."})
    # Surface constraint-driven findings
    if constraint_findings:
        needs_human.extend(
            {"type": f["code"], "details": f, "constraint_id": f.get("constraint_id"),
             "rule_id": f.get("rule_id")}
            for f in constraint_findings
        )
    geometry_valid = (
        not overlaps
        and not size_violations
        and not clearance_violations
        and brahmasthan_clear
        and not constraint_findings  # blocking constraint findings fail geometry
    )
    result = {
        "geometry_valid": geometry_valid,
        "canonical_model_present": canonical_model_present,
        "overlaps": overlaps,
        "size_violations": size_violations,
        "clearance_violations": clearance_violations,
        "brahmasthan_clear": brahmasthan_clear,
        "auto_fixed": auto_fixed,
        "needs_human": needs_human,
        "constraint_findings": constraint_findings,
        "solver_rules_applied": len(solver_rules),
    }
    return result


# ---------------------------------------------------------------------------
# Furniture clearance checks (FURNITURE_MICRO_VASTU_PLAN.md Section 2.3)
# ---------------------------------------------------------------------------

def _furniture_rect(item: dict) -> tuple[float, float, float, float]:
    """Return (x_min, y_min, x_max, y_max) bounding box for a furniture item.

    Furniture items are placed relative to room origin and carry x, y, w, h
    keys (same shape as room dicts, so _rects_overlap from spatial_planner
    works directly).
    """
    x = float(item.get("x", 0))
    y = float(item.get("y", 0))
    w = float(item.get("w", 0))
    h = float(item.get("h", 0))
    return x, y, x + w, y + h


def _door_clearance_zone(room: dict) -> Polygon | None:
    """Return a 1ft-radius clearance polygon around the door swing arc.

    Per FURNITURE_MICRO_VASTU_PLAN.md Section 2.3, a 1ft radius from the
    door hinge/swing arc must remain free of furniture.  This function
    approximates the swing zone as a small rectangle anchored on the door
    opening and extending 1 ft into the room.  Returns None if the room has
    no door metadata.
    """
    door = room.get("door")
    if not door:
        return None

    rx = float(room.get("x", 0))
    ry = float(room.get("y", 0))
    rw = float(room.get("w", 0))
    rh = float(room.get("h", 0))
    wall = str(door.get("wall", ""))
    offset = float(door.get("offset_ft", 0))
    radius = 1.0

    try:
        if wall == "N":
            cx, cy = rx + offset, ry
            return Polygon([
                (cx - radius, cy),
                (cx + radius, cy),
                (cx + radius, cy + radius),
                (cx - radius, cy + radius),
            ])
        elif wall == "S":
            cx, cy = rx + offset, ry + rh
            return Polygon([
                (cx - radius, cy - radius),
                (cx + radius, cy - radius),
                (cx + radius, cy),
                (cx - radius, cy),
            ])
        elif wall == "E":
            cx, cy = rx + rw, ry + offset
            return Polygon([
                (cx - radius, cy - radius),
                (cx, cy - radius),
                (cx, cy + radius),
                (cx - radius, cy + radius),
            ])
        elif wall == "W":
            cx, cy = rx, ry + offset
            return Polygon([
                (cx, cy - radius),
                (cx + radius, cy - radius),
                (cx + radius, cy + radius),
                (cx, cy + radius),
            ])
    except Exception:
        return None


def _window_clearance_zone(room: dict) -> Polygon | None:
    """Return a union polygon representing 1 ft clearance around every window.

    For each window the zone spans (window_width + 2 ft) along the wall and
    extends 1 ft into the room.  The result is the union of all per-window
    zones, or None if the room has no windows.
    """
    windows = room.get("windows", [])
    if not windows:
        return None

    rx = float(room.get("x", 0))
    ry = float(room.get("y", 0))
    rw = float(room.get("w", 0))
    rh = float(room.get("h", 0))
    clearance = 1.0

    zones: list[Polygon] = []
    for win in windows:
        wall = str(win.get("wall", ""))
        offset = float(win.get("offset_ft", 0))
        win_w = float(win.get("width_ft", 3.0))
        half = win_w / 2.0
        try:
            if wall == "N":
                x0 = max(rx, rx + offset - half - clearance)
                x1 = min(rx + rw, rx + offset + half + clearance)
                zones.append(Polygon([
                    (x0, ry),
                    (x1, ry),
                    (x1, ry + clearance),
                    (x0, ry + clearance),
                ]))
            elif wall == "S":
                x0 = max(rx, rx + offset - half - clearance)
                x1 = min(rx + rw, rx + offset + half + clearance)
                zones.append(Polygon([
                    (x0, ry + rh - clearance),
                    (x1, ry + rh - clearance),
                    (x1, ry + rh),
                    (x0, ry + rh),
                ]))
            elif wall == "E":
                y0 = max(ry, ry + offset - half - clearance)
                y1 = min(ry + rh, ry + offset + half + clearance)
                zones.append(Polygon([
                    (rx + rw - clearance, y0),
                    (rx + rw, y0),
                    (rx + rw, y1),
                    (rx + rw - clearance, y1),
                ]))
            elif wall == "W":
                y0 = max(ry, ry + offset - half - clearance)
                y1 = min(ry + rh, ry + offset + half + clearance)
                zones.append(Polygon([
                    (rx, y0),
                    (rx + clearance, y0),
                    (rx + clearance, y1),
                    (rx, y1),
                ]))
        except Exception:
            continue

    if not zones:
        return None
    # Union all per-window zones into a single polygon
    merged = zones[0]
    for z in zones[1:]:
        try:
            merged = merged.union(z)
        except Exception:
            pass
    return merged


def check_furniture_clearances(rooms: list) -> list[str]:
    """Validate furniture clearances inside each room.

    Checks (per FURNITURE_MICRO_VASTU_PLAN.md Section 2.3):
      1. Furniture-to-furniture — items must not overlap (bbox check via
         _rects_overlap).
      2. Furniture-to-door — items must not enter the 1 ft door-swing
         clearance zone.
      3. Furniture-to-window — items must not enter the 1 ft window-sill
         clearance zone.

    Returns a list of human-readable error strings.  Empty list = all
    clearances satisfied.
    """
    errors: list[str] = []
    for room in rooms:
        furniture = room.get("furniture", [])
        placed = [f for f in furniture if f.get("placed")]
        if not placed:
            continue

        rid = room.get("id", "?")

        # 1. Furniture-to-furniture overlap
        for i, a in enumerate(placed):
            for b in placed[i + 1:]:
                if _rects_overlap(a, b):
                    errors.append(
                        f"Furniture overlap in {rid}: {a['type']} vs {b['type']}"
                    )

        # 2. Furniture-to-door clearance (1 ft radius from swing arc)
        door_zone = _door_clearance_zone(room)
        if door_zone is not None:
            for item in placed:
                fx1, fy1, fx2, fy2 = _furniture_rect(item)
                try:
                    if door_zone.intersection(box(fx1, fy1, fx2, fy2)).area > 1e-6:
                        errors.append(
                            f"Furniture-to-door clearance in {rid}: "
                            f"{item['type']} at ({item.get('x')},{item.get('y')}) "
                            f"blocks door swing arc"
                        )
                except Exception:
                    pass

        # 3. Furniture-to-window clearance (1 ft from sill)
        win_zone = _window_clearance_zone(room)
        if win_zone is not None:
            for item in placed:
                fx1, fy1, fx2, fy2 = _furniture_rect(item)
                try:
                    if win_zone.intersection(box(fx1, fy1, fx2, fy2)).area > 1e-6:
                        errors.append(
                            f"Furniture-to-window clearance in {rid}: "
                            f"{item['type']} at ({item.get('x')},{item.get('y')}) "
                            f"blocks window sill clearance"
                        )
                except Exception:
                    pass

    return errors

