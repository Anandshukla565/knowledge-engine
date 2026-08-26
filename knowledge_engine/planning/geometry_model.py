"""Canonical architectural geometry model.

This module converts the ad-hoc ``project_state["rooms"]`` list into a
coherent, unit-explicit building model with rooms, boundary segments,
walls, doors, windows, slabs, shafts, stairs, storeys, and levels.

Design
------
- **Source of truth:** the canonical model is written into
  ``project_state["geometry_model"]``.  Renderers and exporters are updated
  to consume it; legacy ``project_state["rooms"]`` is retained alongside.
- **Stable IDs:** every entity gets a deterministic ID so serialization
  round-trips preserve relationships.
- **Explicit units:** the model carries ``units: "feet"`` and a coordinate
  system header.
- **Walls are first-class:** room-pair boundaries resolve to a single
  canonical boundary segment, which resolves to a single canonical wall.
"""
from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from typing import Any


# Geometry construction uses these local defaults only for wall generation.
# They are not compliance rules and do not claim NBC/legal approval.
DEFAULT_WALL_THICKNESS_FT = {
    "exterior_wall_thickness_ft": 1.0,
    "interior_wall_thickness_ft": 0.5,
}

# ---------------------------------------------------------------------------
# Finding schema helpers
# ---------------------------------------------------------------------------

FINDING_SEVERITY = {"INFO", "WARNING", "ERROR", "CRITICAL"}

FINDING_BLOCKING_MAP = {
    # code -> (blocks_geometry, blocks_issue, blocking_scope)
    "MISSING_INPUT_ROOM_ID":        (True,  True,  ["CONCEPT", "IFC", "REVIT"]),
    "DUPLICATE_INPUT_ROOM_ID":      (True,  True,  ["CONCEPT", "IFC", "REVIT"]),
    "DUPLICATE_SOURCE_RECORD_ID":   (True,  True,  ["CONCEPT", "IFC", "REVIT"]),
    "DUPLICATE_REQUIREMENT_ID":     (True,  True,  ["CONCEPT", "IFC", "REVIT"]),
    "DUPLICATE_CREATION_LINEAGE":   (True,  True,  ["CONCEPT", "IFC", "REVIT"]),
    "MISSING_ROOM_ID":       (True,  True,  ["CONCEPT", "IFC", "REVIT"]),
    "IDENTITY_PROVENANCE_MISSING": (False, False, []),
    "MIXED_UNITS":           (True,  True,  ["CONCEPT", "IFC", "REVIT"]),
    "POLYGON_RECT_MISMATCH": (True,  True,  ["CONCEPT", "IFC", "REVIT"]),
    "VERTICES_POLYGON_MISMATCH": (True, True, ["CONCEPT", "IFC", "REVIT"]),
    "INVALID_POLYGON":       (True,  True,  ["CONCEPT", "IFC", "REVIT"]),
    "NO_GEOMETRY":           (True,  True,  ["CONCEPT", "IFC", "REVIT"]),
    "DUPLICATE_ROOM_ID":     (True,  True,  ["CONCEPT", "IFC", "REVIT"]),
    "LEGACY_AREA_MISMATCH":  (False, False, []),
    "APPROXIMATE_COMPATIBILITY_GEOMETRY": (False, True, ["IFC", "REVIT"]),
    "ROOM_OVERLAP":          (True,  True,  ["CONCEPT", "IFC", "REVIT"]),
    "CLEARANCE_VIOLATION":   (True,  True,  ["CONCEPT", "IFC", "REVIT"]),
    "MINIMUM_SIZE_VIOLATION": (False, False, []),
    "BRAHMAS_THAN_VIOLATION": (False, False, []),
    "MISSING_STOREY":        (True,  True,  ["CONCEPT", "IFC", "REVIT"]),
    "MISSING_LEVEL":         (True,  True,  ["CONCEPT", "IFC", "REVIT"]),
    "ORPHAN_DOOR":           (False, True,  ["IFC", "REVIT"]),
    "ORPHAN_WINDOW":         (False, True,  ["IFC", "REVIT"]),
    "WALL_MISSING_ID":       (True,  True,  ["CONCEPT", "IFC", "REVIT"]),
    "WALL_ZERO_THICKNESS":   (True,  True,  ["CONCEPT", "IFC", "REVIT"]),
    "STACKING_MISALIGNMENT": (False, False, []),
}


def _normalize_finding(
    code: str,
    severity: str,
    entity_type: str,
    entity_ids: list[str],
    description: str,
) -> dict:
    """Return a finding dict with all normalized fields populated."""
    sev = severity.upper()
    if sev not in FINDING_SEVERITY:
        sev = "ERROR"
    blocks_geo, blocks_iss, blocking_scope = FINDING_BLOCKING_MAP.get(
        code, (False, False, [])
    )
    return {
        "code": code,
        "severity": sev,
        "entity_type": entity_type,
        "entity_ids": list(entity_ids),
        "description": description,
        "blocks_geometry": blocks_geo,
        "blocks_issue": blocks_iss,
        "blocking_scope": list(blocking_scope),
    }


def is_geometry_blocking(finding: dict) -> bool:
    """Return True if this finding makes the geometry invalid."""
    return finding.get("blocks_geometry", False)


def blocks_issue(finding: dict, issue_type: str) -> bool:
    """Return True if this finding blocks a specific issue type."""
    scope = finding.get("blocking_scope", [])
    return issue_type.upper() in [s.upper() for s in scope]


def check_export_eligibility(
    model: dict,
    export_type: str,
) -> tuple[bool, list[dict]]:
    """Check whether a model is eligible for a specific export type.

    Args:
        model: Canonical geometry model dict.
        export_type: One of "IFC", "REVIT", "DXF", "SVG", "DEBUG".

    Returns:
        (eligible, blocking_findings) — eligible is True only if no findings
        block the requested export_type.  DEBUG is always eligible.
    """
    if export_type.upper() == "DEBUG":
        return True, []

    findings = model.get("validation_findings", [])
    blocking = [
        f for f in findings
        if f.get("blocks_issue", False) and blocks_issue(f, export_type)
    ]
    return len(blocking) == 0, blocking


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EPS = 1e-6


def _f(x: Any) -> float:
    """Coerce to float, default 0.0."""
    if x is None:
        return 0.0
    return float(x)


def _sig(x: float) -> float:
    """Round small values to zero."""
    return 0.0 if abs(x) < _EPS else x


def _deterministic_id(*parts: str) -> str:
    """Stable, collision-free ID from ordered string parts."""
    raw = "|".join(str(p) for p in parts)
    h = hashlib.sha1(raw.encode()).hexdigest()[:8]
    slug = "_".join(str(p).replace(" ", "_")[:20] for p in parts)
    return f"id_{slug}_{h}"


# Namespace prefixes used by _provenance_entity_id
_PROVENANCE_PREFIX = {
    "source_record_id": "src",
    "creation_lineage": "lin",
    "requirement_id": "req",
    "req_id": "req",
}


def _provenance_entity_id(provenance_type: str, provenance_value: str) -> str:
    """Return a stable, namespaced entity ID derived from a provenance field.

    Example: ``_provenance_entity_id("source_record_id", "REQ-BEDROOM-01")``
    produces ``"room_src_REQ-BEDROOM-01_<hash>"``.

    The raw provenance value is NEVER used directly as the canonical model ID.
    """
    value = str(provenance_value).strip()
    if not value:
        return ""
    prefix = _PROVENANCE_PREFIX.get(provenance_type, "raw")
    raw = f"{provenance_type}|{value}"
    h = hashlib.sha1(raw.encode()).hexdigest()[:8]
    slug = value.replace(" ", "_")[:20]
    return f"room_{prefix}_{slug}_{h}"


def _rect_polygon(room: dict) -> list[tuple[float, float]]:
    """Return CCW polygon from room x/y/w/h."""
    x, y, w, h = _f(room.get("x")), _f(room.get("y")), _f(room.get("w")), _f(room.get("h"))
    return [
        (_sig(x), _sig(y)),
        (_sig(x + w), _sig(y)),
        (_sig(x + w), _sig(y + h)),
        (_sig(x), _sig(y + h)),
    ]


def _polygon(room: dict) -> tuple[list[tuple[float, float]], list[dict]]:
    """Return polygon vertices for a room, plus zero or more conflict findings.

    Priority and conflict rules (applied in order):

    1. ``vertices`` — highest priority, explicit per-vertex list.
    2. ``polygon``  — canonical pre-built polygon.
    3. ``x/y/w/h``  — legacy rectangle, converted to 4-point polygon.

    Returns ``(polygon, findings)`` where ``findings`` is a list of all
    conflict findings discovered during normalization.  A room may have
    multiple simultaneous defects (representation conflicts, invalid
    geometry, missing units, duplicate ID — each is reported separately).

    Conflict handling
    -----------------
    * Matching representations  → use the higher-priority one, no finding.
    * Mismatching representations → use the higher-priority one, emit a
      blocking finding.
    * Invalid higher-priority representation → reject it, fall back to
      the next-lower representation, emit a blocking finding.
    * Nothing available → emit a blocking ``NO_GEOMETRY`` finding.
    """
    verts = room.get("vertices")
    poly = room.get("polygon")
    has_rect = any(room.get(k) is not None for k in ("x", "y", "w", "h"))
    room_id = room.get("id")
    findings: list[dict] = []

    # Detect duplicate room ID (blocking)
    if not room_id:
        findings.append(_normalize_finding(
            "MISSING_ROOM_ID", "error", "room", [room_id or "?"],
            f"Room {room_id or '?'} has no 'id' field.",
        ))

    # Detect mixed units (blocking)
    unit_fields = {"x_ft", "y_ft", "w_ft", "h_ft", "x_m", "y_m", "w_m", "h_m"}
    present_units = set()
    for key in room:
        if key in unit_fields:
            if key.endswith("_m"):
                present_units.add("metric")
            elif key.endswith("_ft"):
                present_units.add("imperial")
    if len(present_units) > 1:
        findings.append(_normalize_finding(
            "MIXED_UNITS", "error", "room", [room_id or "?"],
            f"Room has mixed unit fields: {sorted(present_units)}.",
        ))

    # --- vertices (highest priority) ---
    if verts and len(verts) >= 3:
        vpoly = [(_f(vx), _f(vy)) for vx, vy in verts]
        if not _is_simple_polygon(vpoly):
            findings.append(_normalize_finding(
                "INVALID_POLYGON", "error", "room", [room_id or "?"],
                f"Room {room_id}: vertices are self-intersecting or degenerate; falling back to rectangle.",
            ))
            # Fall through to polygon or rect below
        else:
            if poly and len(poly) >= 3:
                ppoly = [(_f(vx), _f(vy)) for vx, vy in poly]
                if not _polygons_equal(vpoly, ppoly):
                    findings.append(_normalize_finding(
                        "VERTICES_POLYGON_MISMATCH", "error", "room", [room_id or "?"],
                        f"Room {room_id}: vertices and polygon disagree; using vertices.",
                    ))
            return vpoly, findings

    # --- polygon (medium priority) ---
    if poly and len(poly) >= 3:
        ppoly = [(_f(vx), _f(vy)) for vx, vy in poly]
        if not _is_simple_polygon(ppoly):
            if has_rect:
                findings.append(_normalize_finding(
                    "INVALID_POLYGON", "error", "room", [room_id or "?"],
                    f"Room {room_id}: polygon is self-intersecting or degenerate; falling back to rectangle.",
                ))
                return _rect_polygon(room), findings
            findings.append(_normalize_finding(
                "INVALID_POLYGON", "error", "room", [room_id or "?"],
                f"Room {room_id}: polygon is self-intersecting or degenerate; no rectangle fallback available.",
            ))
            return ppoly, findings
        if has_rect:
            rpoly = _rect_polygon(room)
            if not _polygons_equal(ppoly, rpoly):
                # Mismatching polygon and rect — polygon wins
                findings.append(_normalize_finding(
                    "POLYGON_RECT_MISMATCH", "error", "room", [room_id or "?"],
                    f"Room {room_id}: polygon and rectangle disagree; using polygon.",
                ))
        return ppoly, findings

    # --- legacy rectangle (lowest priority) ---
    if has_rect:
        return _rect_polygon(room), findings

    # Nothing to work with
    findings.append(_normalize_finding(
        "NO_GEOMETRY", "error", "room", [room_id or "?"],
        f"Room {room_id}: no polygon, vertices, or rectangle provided.",
    ))
    return [(0.0, 0.0), (0.0, 0.0), (0.0, 0.0)], findings


def _is_simple_polygon(poly: list[tuple[float, float]]) -> bool:
    """Return True if the polygon is simple (non-self-intersecting) and non-degenerate."""
    if len(poly) < 3:
        return False
    # Check for zero area (degenerate)
    area = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        area += x1 * y2 - x2 * y1
    if abs(area) < 1e-4:
        return False
    # Check for duplicate consecutive vertices
    for i in range(len(poly)):
        j = (i + 1) % len(poly)
        if _point_eq(poly[i], poly[j]):
            return False
    # Self-intersection check: no two non-adjacent edges should cross
    edges = _edges_of(poly)
    for i in range(len(edges)):
        for j in range(i + 1, len(edges)):
            if _edges_share_vertex(edges[i], edges[j]):
                continue
            if _segments_intersect(edges[i], edges[j]):
                return False
    return True


def _edges_share_vertex(a: tuple[tuple, tuple], b: tuple[tuple, tuple]) -> bool:
    return _point_eq(a[0], b[0]) or _point_eq(a[0], b[1]) or \
           _point_eq(a[1], b[0]) or _point_eq(a[1], b[1])


def _segments_intersect(
    a: tuple[tuple, tuple], b: tuple[tuple, tuple]
) -> bool:
    """Return True if segments a and b intersect (excluding shared endpoints)."""
    ax, ay = a[0]
    ax2, ay2 = a[1]
    bx, by = b[0]
    bx2, by2 = b[1]

    # Bounding box rejection
    if max(ax, ax2) < min(bx, bx2) or min(ax, ax2) > max(bx, bx2):
        return False
    if max(ay, ay2) < min(by, by2) or min(ay, ay2) > max(by, by2):
        return False

    # Orientation tests
    o1 = _orient(ax, ay, ax2, ay2, bx, by)
    o2 = _orient(ax, ay, ax2, ay2, bx2, by2)
    o3 = _orient(bx, by, bx2, by2, ax, ay)
    o4 = _orient(bx, by, bx2, by2, ax2, ay2)

    if o1 == 0 or o2 == 0 or o3 == 0 or o4 == 0:
        return False  # collinear — already filtered by simple check
    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0)


def _orient(ax, ay, ax2, ay2, bx, by) -> float:
    """Cross product for orientation."""
    return (ax2 - ax) * (by - ay) - (ay2 - ay) * (bx - ax)


def _polygons_equal(
    a: list[tuple[float, float]], b: list[tuple[float, float]], tol: float = 0.05
) -> bool:
    """Return True if two polygons are equal within tolerance.

    Supports:
    - Same vertex count and same winding: cyclic shift match.
    - Same vertex count and opposite winding: reversed cyclic shift match.
    - Rectangle vs rectangle at different vertex order: handled by the
      cyclic/reversed checks above.

    Equal bounding boxes alone do NOT establish geometric equality.
    Two polygons with the same bbox but different vertex shapes will
    correctly return False.
    """
    if len(a) != len(b):
        return False
    # Try all forward cyclic shifts of b against a
    for shift in range(len(b)):
        if all(_point_eq(a[i], b[(i + shift) % len(b)]) for i in range(len(a))):
            return True
    # Try all reversed cyclic shifts of b against a (opposite winding)
    brev = list(reversed(b))
    for shift in range(len(brev)):
        if all(_point_eq(a[i], brev[(i + shift) % len(brev)]) for i in range(len(a))):
            return True
    return False


def _bbox(poly: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    """Return (x, y, w, h) bounding box from a polygon."""
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    x = min(xs)
    y = min(ys)
    w = max(xs) - x
    h = max(ys) - y
    return _sig(x), _sig(y), _sig(w), _sig(h)


def _polygon_area(poly: list[tuple[float, float]]) -> float:
    """Return the absolute area of a polygon via the shoelace formula."""
    n = len(poly)
    if n < 3:
        return 0.0
    return abs(
        sum(
            poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1]
            for i in range(n)
        )
    ) / 2.0


def _bbox_compatibility(poly: list[tuple[float, float]], rect: dict) -> dict:
    """Return explicit bbox compatibility metadata.

    For rectangular polygons (polygon == rect within tolerance),
    exact_representation is True.
    For irregular polygons, exact_representation is False and
    APPROXIMATE_COMPATIBILITY_GEOMETRY should be emitted by the caller.
    """
    n = len(poly)
    if n < 3:
        return {"derived": True, "exact_representation": False}
    poly_area = _polygon_area(poly)
    rect_area = rect["w"] * rect["h"]
    if rect_area <= _EPS:
        return {"derived": True, "exact_representation": True}
    ratio = poly_area / rect_area
    return {
        "derived": True,
        "exact_representation": ratio > 0.999,  # within 0.1% of bbox area
    }


def _rect_from_polygon(poly: list[tuple[float, float]]) -> dict:
    """Derive x/y/w/h rectangle fields from a polygon.

    Returns a dict with ``x``, ``y``, ``w``, ``h``.  These are *derived*
    values — they must not be stored independently, because ``polygon`` is
    the authoritative representation.
    """
    x, y, w, h = _bbox(poly)
    return {"x": x, "y": y, "w": w, "h": h}


def _check_polygon_rect_mismatch(room: dict, poly: list[tuple[float, float]]) -> dict | None:
    """Detect deliberate polygon/rectangle mismatch.

    DEPRECATED: ``_polygon()`` now handles conflict detection internally
    and returns findings as a tuple element.  This function is kept for
    backward compatibility with external callers; it compares the
    supplied ``poly`` against the room's x/y/w/h rectangle and returns
    a finding dict when they disagree beyond tolerance, or ``None`` when
    they agree.
    """
    rx = _f(room.get("x"))
    ry = _f(room.get("y"))
    rw = _f(room.get("w"))
    rh = _f(room.get("h"))
    if rw <= 0 and rh <= 0 and not room.get("x") and not room.get("y"):
        return None  # no rectangle to compare
    dx, dy, dw, dh = _bbox(poly)
    tol = 0.05  # 0.05 ft tolerance for rounding noise
    diffs = []
    if abs(rx - dx) > tol:
        diffs.append(f"x {rx} vs {dx}")
    if abs(ry - dy) > tol:
        diffs.append(f"y {ry} vs {dy}")
    if abs(rw - dw) > tol:
        diffs.append(f"w {rw} vs {dw}")
    if abs(rh - dh) > tol:
        diffs.append(f"h {rh} vs {dh}")
    if diffs:
        return _normalize_finding(
            "POLYGON_RECT_MISMATCH", "error", "room", [room.get("id", "?")],
            f"Polygon and rectangle disagree: {', '.join(diffs)}.",
        )
    return None


def _edge_id(p1: tuple[float, float], p2: tuple[float, float]) -> str:
    """Stable key for an undirected edge."""
    a, b = sorted([p1, p2])
    return f"e_{a[0]:.3f}_{a[1]:.3f}_{b[0]:.3f}_{b[1]:.3f}"


def _point_eq(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return math.hypot(a[0] - b[0], a[1] - b[1]) < _EPS


def _edges_of(poly: list[tuple[float, float]]) -> list[tuple[tuple, tuple]]:
    """Return edges (p_i, p_{i+1}) of a polygon."""
    return [(poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly))]


def _edge_vector(e: tuple[tuple, tuple]) -> tuple[float, float]:
    return (_sig(e[1][0] - e[0][0]), _sig(e[1][1] - e[0][1]))


def _edge_length(e: tuple[tuple, tuple]) -> float:
    return math.hypot(e[1][0] - e[0][0], e[1][1] - e[0][1])


def _midpoint(e: tuple[tuple, tuple]) -> tuple[float, float]:
    return ((_sig(e[0][0] + e[1][0]) / 2.0), (_sig(e[0][1] + e[1][1]) / 2.0))


def _projection_overlap(a: tuple[tuple, tuple], b: tuple[tuple, tuple]) -> float:
    """Return overlap length when two parallel edges are projected onto the shared axis."""
    va = _edge_vector(a)
    vb = _edge_vector(b)
    axis = va if (abs(va[0]) > abs(va[1])) else (va[1], va[0])  # primary axis
    # Project endpoints onto axis
    def proj(e):
        return e[0][0] * axis[0] + e[0][1] * axis[1], e[1][0] * axis[0] + e[1][1] * axis[1]
    pa = sorted(proj(a))
    pb = sorted(proj(b))
    return max(0.0, min(pa[1], pb[1]) - max(pa[0], pb[0]))


def _normal_cardinal(e: tuple[tuple, tuple]) -> str | None:
    """Return N/S/E/W for an axis-aligned edge.

    Convention matches the legacy room wall labels (door/window metadata):
    - Horizontal edge with x increasing → "S" (south-facing bottom wall)
    - Horizontal edge with x decreasing → "N" (north-facing top wall)
    - Vertical edge with y increasing → "E" (east-facing right wall)
    - Vertical edge with y decreasing → "W" (west-facing left wall)
    """
    v = _edge_vector(e)
    if abs(v[0]) < _EPS and abs(v[1]) + _EPS > 0:
        return "E" if v[1] > 0 else "W"
    if abs(v[1]) < _EPS and abs(v[0]) + _EPS > 0:
        return "S" if v[0] > 0 else "N"
    return None


# ---------------------------------------------------------------------------
# Storey / level
# ---------------------------------------------------------------------------

def _build_storeys(rooms: list[dict], plot: dict) -> list[dict]:
    """Build storey list from rooms."""
    storey_index: dict[int, int] = {}
    storeys: list[dict] = []
    elevation = 0.0
    for r in sorted(rooms, key=lambda r: r.get("floor", 0)):
        fl = int(r.get("floor", 0))
        if fl not in storey_index:
            sid = _deterministic_id("storey", str(fl))
            storey_index[fl] = len(storeys)
            storeys.append({
                "id": sid,
                "index": fl,
                "elevation_ft": round(elevation, 3),
            })
            # Assume 10 ft floor-to-floor
            elevation += _f(plot.get("floor_height_ft", 10.0))
    return storeys


def _build_levels(storeys: list[dict], plot: dict) -> list[dict]:
    """Build level list from storeys.  One level per storey for now."""
    levels = []
    for st in storeys:
        levels.append({
            "id": _deterministic_id("level", st["id"]),
            "name": f"Level {st['index']}",
            "storey_ids": [st["id"]],
        })
    return levels


# ---------------------------------------------------------------------------
# Boundary segments
# ---------------------------------------------------------------------------

def _build_boundary_segments(
    rooms: list[dict],
    plot: dict,
    storeys: list[dict],
) -> list[dict]:
    """Derive boundary segments from room polygons.

    Each unique undirected edge that belongs to one or more rooms becomes
    a boundary segment.  Interior edges belong to exactly two rooms;
    exterior edges belong to exactly one room and have exterior_context=True.
    """
    plot_w = _f(plot.get("usable_width_ft") or plot.get("width_ft"))
    plot_d = _f(plot.get("usable_depth_ft") or plot.get("depth_ft"))

    room_map = {r["_processing_key"]: r for r in rooms}
    # Group room polygons by floor
    by_floor: dict[int, list[dict]] = defaultdict(list)
    for r in rooms:
        by_floor[r.get("floor", 0)].append(r)

    edge_rooms: dict[str, list[str]] = defaultdict(list)

    for fl, floor_rooms in by_floor.items():
        for room in floor_rooms:
            poly, _ = _polygon(room)
            for edge in _edges_of(poly):
                eid = _edge_id(edge[0], edge[1])
                edge_rooms[eid].append(room["_processing_key"])

    segments: list[dict] = []
    seg_index: dict[str, dict] = {}

    for eid, rids in edge_rooms.items():
        # Compute the geometric edge from the first room's polygon
        first_room = room_map[rids[0]]
        poly, _ = _polygon(first_room)
        for edge in _edges_of(poly):
            if _edge_id(edge[0], edge[1]) == eid:
                geo_edge = edge
                break
        else:
            continue

        is_exterior = len(rids) == 1
        # Verify exterior edges touch the plot boundary
        if is_exterior:
            mid = _midpoint(geo_edge)
            near_boundary = (
                abs(mid[0]) < _EPS or abs(mid[0] - plot_w) < _EPS or
                abs(mid[1]) < _EPS or abs(mid[1] - plot_d) < _EPS
            )
            if not near_boundary:
                # Interior edge that only belongs to one room (L-shaped gap, etc.)
                is_exterior = False

        # Find storey_id from first room
        first_floor = first_room.get("floor", 0)
        storey_id = None
        for st in storeys:
            if st["index"] == first_floor:
                storey_id = st["id"]
                break

        seg = {
            "id": eid,
            "geometry": [list(geo_edge[0]), list(geo_edge[1])],
            "adjacent_room_ids": sorted(rids),
            "exterior_context": is_exterior,
            "storey_id": storey_id,
            "length_ft": round(
                math.hypot(
                    geo_edge[1][0] - geo_edge[0][0],
                    geo_edge[1][1] - geo_edge[0][1],
                ),
                4,
            ),
        }
        segments.append(seg)
        seg_index[eid] = seg

    return segments


# ---------------------------------------------------------------------------
# Walls
# ---------------------------------------------------------------------------

def _build_walls(
    boundary_segments: list[dict],
    rooms: list[dict],
    plot: dict,
    building_codes: dict | None = None,
) -> list[dict]:
    """Create one wall per boundary segment.

    Each boundary segment is the shared edge between one or more rooms.
    We create one wall entity per segment; walls are NOT merged across
    gaps because non-adjacent segments may belong to different room
    sets and merging them produces incorrect adjacency data.
    """
    if building_codes is None:
        building_codes = DEFAULT_WALL_THICKNESS_FT

    exterior_thickness = _f(building_codes.get("exterior_wall_thickness_ft", 1.0))
    interior_thickness = _f(building_codes.get("interior_wall_thickness_ft", 0.5))

    walls: list[dict] = []

    for seg in boundary_segments:
        geo = seg["geometry"]
        is_exterior = seg["exterior_context"]
        wall_type = "shear" if is_exterior else "partition"
        thickness = exterior_thickness if is_exterior else interior_thickness

        length = round(
            math.hypot(
                geo[1][0] - geo[0][0],
                geo[1][1] - geo[0][1],
            ),
            4,
        )

        wid = _deterministic_id(
            "wall", seg["storey_id"], wall_type,
            geo[0][0], geo[0][1], geo[1][0], geo[1][1],
        )

        walls.append({
            "id": wid,
            "boundary_segment_ids": [seg["id"]],
            "type": wall_type,
            "thickness_ft": round(thickness, 4),
            "exterior": is_exterior,
            "structural": is_exterior,
            "adjacent_room_ids": seg["adjacent_room_ids"],
            "geometry": [list(geo[0]), list(geo[1])],
            "storey_id": seg.get("storey_id"),
            "length_ft": length,
        })

    return walls


# ---------------------------------------------------------------------------
# Doors and windows
# ---------------------------------------------------------------------------

def _build_openings(
    rooms: list[dict],
    walls: list[dict],
    building_codes: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """Build door and window entities from room metadata, matched to walls."""
    if building_codes is None:
        building_codes = DEFAULT_WALL_THICKNESS_FT

    # Wall lookup: by storey + midpoint proximity + direction
    wall_by_key: dict[str, dict] = {}
    for w in walls:
        geo = w["geometry"]
        mid = (
            round((geo[0][0] + geo[-1][0]) / 2, 3),
            round((geo[0][1] + geo[-1][1]) / 2, 3),
        )
        card = _normal_cardinal((tuple(geo[0]), tuple(geo[-1])))
        if card:
            wall_by_key[(w.get("storey_id"), card, mid[0] if card in ("N", "S") else mid[1])] = w

    doors: list[dict] = []
    windows: list[dict] = []
    default_door_w = _f(building_codes.get("min_door_width_ft", 3.0))
    default_door_h = _f(building_codes.get("min_door_height_ft", 6.8))
    default_win_w = _f(building_codes.get("min_window_width_ft", 3.0))
    default_win_h = 3.0  # typical sill-to-sill

    for room in rooms:
        # Doors
        door = room.get("door")
        if door:
            wall_dir = door.get("wall", "").upper()
            offset = _f(door.get("offset_ft"))
            # Find host wall: match by direction + axis position
            rid = room["_processing_key"]
            host = _find_host_wall(walls, room, wall_dir, offset)
            if host:
                connects = tuple(sorted([rid, rid]))  # default: connects to self
                # Find adjacent room on shared wall
                for other in host.get("adjacent_room_ids", []):
                    if other != rid:
                        connects = tuple(sorted([rid, other]))
                        break

                doors.append({
                    "id": _deterministic_id("door", rid, wall_dir, f"{offset:.2f}"),
                    "host_wall_id": host["id"],
                    "position_ft": round(offset, 4),
                    "width_ft": round(_f(door.get("width_ft", default_door_w)), 4),
                    "height_ft": round(_f(door.get("height_ft", default_door_h)), 4),
                    "connects": list(connects),
                    "elevation_ft": 0.0,
                    "swings_into": door.get("swings_into", rid),
                })

        # Windows
        for idx, win in enumerate(room.get("windows", [])):
            wall_dir = win.get("wall", "").upper()
            offset = _f(win.get("offset_ft"))
            win_w = _f(win.get("width_ft", default_win_w))
            host = _find_host_wall(walls, room, wall_dir, offset)
            if host:
                windows.append({
                    "id": _deterministic_id("window", rid, wall_dir, str(idx), f"{offset:.2f}"),
                    "host_wall_id": host["id"],
                    "position_ft": round(offset, 4),
                    "width_ft": round(win_w, 4),
                    "height_ft": round(default_win_h, 4),
                    "elevation_ft": 3.0,  # typical sill
                    "room_id": rid,
                })

    return doors, windows


def _find_host_wall(
    walls: list[dict],
    room: dict,
    wall_dir: str,
    offset_ft: float,
) -> dict | None:
    """Find the wall that hosts a door/window for a given room and direction.

    Matching strategy:
    1. Filter walls by storey, direction, and room adjacency.
    2. Check the wall is on the correct side of the room (axis position).
    3. Check the opening offset falls within the wall's extent.
    """
    rid = room["_processing_key"]
    storey_id = _storey_id_for_room(room)
    room_poly, _ = _polygon(room)
    poly_xs = [p[0] for p in room_poly]
    poly_ys = [p[1] for p in room_poly]
    rx, ry = min(poly_xs), min(poly_ys)
    rw = max(poly_xs) - rx
    rh = max(poly_ys) - ry

    # Axis position of the room side for each direction
    if wall_dir == "N":
        axis_pos = ry + rh
        wall_extent_min = rx
        wall_extent_max = rx + rw
    elif wall_dir == "S":
        axis_pos = ry
        wall_extent_min = rx
        wall_extent_max = rx + rw
    elif wall_dir == "E":
        axis_pos = rx + rw
        wall_extent_min = ry
        wall_extent_max = ry + rh
    elif wall_dir == "W":
        axis_pos = rx
        wall_extent_min = ry
        wall_extent_max = ry + rh
    else:
        return None

    candidates = []
    for w in walls:
        if w.get("storey_id") != storey_id:
            continue
        if rid not in w.get("adjacent_room_ids", []):
            continue
        geo = w["geometry"]
        card = _normal_cardinal((tuple(geo[0]), tuple(geo[-1])))
        if card != wall_dir:
            continue
        # Check axis position matches
        if wall_dir in ("N", "S"):
            wall_axis = geo[0][1]
        else:
            wall_axis = geo[0][0]
        if abs(wall_axis - axis_pos) > 1.0:
            continue
        # Check offset falls within wall extent
        length = w.get("length_ft", 0)
        if wall_dir in ("N", "S"):
            wall_start = min(geo[0][0], geo[-1][0])
            pos_on_wall = offset_ft + wall_extent_min
        else:
            wall_start = min(geo[0][1], geo[-1][1])
            pos_on_wall = offset_ft + wall_extent_min
        offset_from_wall_start = pos_on_wall - wall_start
        if -1.0 <= offset_from_wall_start <= length + 1.0:
            candidates.append((abs(offset_from_wall_start - length / 2), w))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def _storey_id_for_room(room: dict) -> str | None:
    """Compute storey_id for a room (deterministic, mirrors _build_storeys)."""
    fl = room.get("floor", 0)
    return _deterministic_id("storey", str(fl))


def _resolve_identity(room: dict) -> dict:
    """Resolve canonical identity for a room without mutating ``room["id"]``.

    Returns a dict with keys:
        processing_key   — stable key used for internal topology indexing
        canonical_id     — the stable entity ID for the canonical room record
        has_stable_provenance — True if any stable provenance field was found
        had_missing_id   — True only when no id and no provenance at all
        identity         — dict of provenance fields and metadata for output

    Provenance hierarchy (first match wins):
      1. ``source_record_id`` (highest authority — external persistence identifier)
      2. ``creation_lineage`` (immutable trace to origin)
      3. ``requirement_id`` / ``req_id`` (design intent identifier)
      4. Existing ``room["id"]`` if non-empty (forward-compat: imported ID)
      5. Deterministic fallback from type + position (last resort)

    Stable provenance values are NEVER used directly as canonical IDs.  They
    are wrapped via ``_provenance_entity_id`` which produces a namespaced
    hash-based identifier (e.g. ``room_src_REQ-BEDROOM-01_<hash>``).
    """
    # Capture original provenance before any mutation
    source_record_id = room.get("source_record_id")
    creation_lineage = room.get("creation_lineage")
    requirement_id = room.get("requirement_id") or room.get("req_id")
    original_id = room.get("id")

    # Provenance conflict detection: if two different provenance fields are
    # present, the highest-priority one wins.  The others are preserved in
    # identity metadata but do not become the canonical id basis.

    # Resolve the processing key — used for internal lookups during
    # topology construction (boundary segments, walls, shafts).
    # The processing key follows the same hierarchy but uses raw values
    # (it is always stripped and never written to the output).
    if source_record_id:
        _pk = str(source_record_id).strip()
        _id_basis = "source_record_id"
    elif creation_lineage:
        _pk = str(creation_lineage).strip()
        _id_basis = "creation_lineage"
    elif requirement_id:
        _pk = str(requirement_id).strip()
        _id_basis = "requirement_id"
    elif original_id and str(original_id).strip():
        _pk = str(original_id).strip()
        _id_basis = "explicit_id"
    else:
        _pk = _deterministic_id("room", room.get("type", ""), str(room.get("x", 0)), str(room.get("y", 0)))
        _id_basis = "deterministic_fallback"

    # Compute the canonical entity ID from the chosen provenance basis.
    # Raw provenance values are wrapped in a namespaced hash — they are
    # NEVER used directly as the canonical model ID.
    if _id_basis == "source_record_id":
        canonical_id = _provenance_entity_id("source_record_id", source_record_id)
    elif _id_basis == "creation_lineage":
        canonical_id = _provenance_entity_id("creation_lineage", creation_lineage)
    elif _id_basis == "requirement_id":
        canonical_id = _provenance_entity_id("requirement_id", requirement_id)
    elif _id_basis == "explicit_id":
        canonical_id = str(original_id).strip()
    else:
        canonical_id = _pk  # already a deterministic id

    had_missing_id = _id_basis == "deterministic_fallback"
    has_stable_provenance = _id_basis in ("source_record_id", "creation_lineage", "requirement_id")

    stability = "STABLE" if has_stable_provenance else (
        "UNSTABLE" if had_missing_id else "EXPLICIT"
    )

    identity = {
        "source_record_id": source_record_id,
        "requirement_id": requirement_id,
        "creation_lineage": creation_lineage,
        "identity_basis": _id_basis,
        "stability": stability,
    }

    return {
        "processing_key": _pk,
        "canonical_id": canonical_id,
        "has_stable_provenance": has_stable_provenance,
        "had_missing_id": had_missing_id,
        "identity": identity,
    }


# ---------------------------------------------------------------------------
# Slabs, shafts, stairs
# ---------------------------------------------------------------------------

def _build_shafts(rooms: list[dict]) -> list[dict]:
    """Identify stair/lift shafts from room metadata.

    Each shaft entity is per-room (per floor).  ``floor_served`` records
    the floor index so downstream consumers can build vertical transport
    stacks without re-deriving the floor from ``storey_id``.
    """
    shaft_types = {"Staircase", "Lift", "lift", "staircase", "stair"}
    shafts = []
    for room in rooms:
        rtype = room.get("type", "")
        if rtype in shaft_types or any(st in rtype.lower() for st in ("stair", "lift", "shaft")):
            poly, _ = _polygon(room)
            services = ["vertical_transport"]
            if "stair" in rtype.lower():
                services = ["stairs"]
            elif "lift" in rtype.lower():
                services = ["lift"]
            shafts.append({
                "id": _deterministic_id("shaft", room["_processing_key"]),
                "polygon": [list(p) for p in poly],
                "services": services,
                "room_id": room["_canonical_id"],
                "storey_id": _storey_id_for_room(room),
                "floor_served": [room.get("floor", 0)],
            })
    return shafts


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_canonical_model(
    project_state: dict,
    building_codes: dict | None = None,
) -> dict:
    """Build the canonical geometry model from project_state.

    Returns a dict matching the canonical schema with schema_version,
    units, coordinate_system, building, storeys, levels, rooms,
    boundary_segments, walls, doors, windows, slabs, shafts, stairs,
    stable_ids, generation_metadata, and validation_findings.
    """
    rooms = project_state.get("rooms", [])
    plot = project_state.get("plot", {})
    plot_w = _f(plot.get("usable_width_ft") or plot.get("width_ft"))
    plot_d = _f(plot.get("usable_depth_ft") or plot.get("depth_ft"))

    # When a room has no id at all, polygon normalization sets MISSING_ROOM_ID.
    # That happens BELOW (in _polygon), so we only need to pre-assign ids here
    # when the room has *some* provenance.  Rooms with no id and no provenance
    # are left id-less so their MISSING_ROOM_ID finding is preserved.

    # Resolve identity per room before topology construction.
    # Stores _processing_key + canonical_id metadata on each room for the
    # rest of the pipeline.  The raw room["id"] is NEVER mutated here.
    # Provenance-based duplicates are tracked and emit DUPLICATE_SOURCE_*_ID
    # findings in the canonical-room loop below.
    source_seen: dict[str, dict] = {}  # source_record_id -> first room meta
    requirement_seen: dict[str, dict] = {}
    lineage_seen: dict[str, dict] = {}
    explicit_id_groups: dict[str, list[dict]] = defaultdict(list)  # raw id -> resolved list

    for _room in rooms:
        _resolved = _resolve_identity(_room)
        _room["_processing_key"] = _resolved["processing_key"]
        _room["_canonical_id"] = _resolved["canonical_id"]
        _room["_identity"] = _resolved["identity"]
        _room["_has_stable_provenance"] = _resolved["has_stable_provenance"]
        _room["_had_missing_id"] = _resolved["had_missing_id"]

        # Track explicit-id duplicates across distinct rooms
        raw_id = _room.get("id")
        if raw_id and str(raw_id).strip():
            explicit_id_groups[str(raw_id).strip()].append(_resolved)

        src = _resolved["identity"].get("source_record_id")
        if src:
            if src in source_seen:
                _room["_duplicate_source_record_id"] = source_seen[src]
            else:
                source_seen[src] = _resolved
        req = _resolved["identity"].get("requirement_id")
        if req:
            if req in requirement_seen:
                _room["_duplicate_requirement_id"] = requirement_seen[req]
            else:
                requirement_seen[req] = _resolved
        lin = _resolved["identity"].get("creation_lineage")
        if lin:
            if lin in lineage_seen:
                _room["_duplicate_creation_lineage"] = lineage_seen[lin]
            else:
                lineage_seen[lin] = _resolved

    # Emit DUPLICATE_INPUT_ROOM_ID for raw explicit IDs that appear on
    # multiple rooms with distinct provenance or positions.
    _duplicate_input_id_findings: list[dict] = []
    for raw_id, resolved_list in explicit_id_groups.items():
        if len(resolved_list) <= 1:
            continue
        entity_ids = [r["canonical_id"] for r in resolved_list]
        _duplicate_input_id_findings.append(_normalize_finding(
            "DUPLICATE_INPUT_ROOM_ID",
            "ERROR",
            "room",
            entity_ids,
            f"Input room id='{raw_id}' appears on {len(resolved_list)} rooms "
            f"with distinct provenance/position: canonical IDs {entity_ids}. "
            f"The original defect is preserved in validation_findings.",
        ))

    storeys = _build_storeys(rooms, plot)
    levels = _build_levels(storeys, plot)

    boundary_segments = _build_boundary_segments(rooms, plot, storeys)
    walls = _build_walls(boundary_segments, rooms, plot, building_codes)
    doors, windows = _build_openings(rooms, walls, building_codes)
    slabs = []
    shafts = _build_shafts(rooms)
    stairs = []

    # Conflict findings are accumulated during room normalization (before
    # x/y/w/h derivation), then merged with structural validation findings.
    findings: list[dict] = _duplicate_input_id_findings

    # ID generation uses _room_fingerprint for stable collision resolution.
    # See ID hierarchy and collision logic below the canonical_rooms loop.

    # Room records in canonical form.
    # Mismatch detection happens BEFORE normalization so the conflict
    # finding is preserved and cannot be erased by x/y/w/h derivation.
    # See step 1-5 in _polygon() docstring.
    canonical_rooms = []
    for room in rooms:
        poly, room_findings = _polygon(room)
        findings.extend(room_findings)

        # ---- ID generation ----
        # Emit MISSING_INPUT_ROOM_ID for input rooms with no id and no provenance.
        if room.get("_had_missing_id"):
            findings.append(_normalize_finding(
                "MISSING_INPUT_ROOM_ID",
                "ERROR",
                "room",
                [room["_canonical_id"]],
                f"Room {room.get('type', '?')} has no 'id' field.",
            ))
        if not room.get("_has_stable_provenance"):
            findings.append(_normalize_finding(
                "IDENTITY_PROVENANCE_MISSING",
                "INFO",
                "room",
                [room["_canonical_id"]],
                f"Room {room.get('type', '?')} at ({room.get('x', 0)}, {room.get('y', 0)}): "
                f"no stable provenance (source_record_id, requirement_id, creation_lineage). "
                f"Logical identity cannot be guaranteed across reorder or geometry modification. "
                f"Canonical ID derived from geometry fallback: '{room['_processing_key']}'.",
            ))

        # ---- Source-identity conflict findings ----
        src = room["_identity"].get("source_record_id")
        if src and room.get("_duplicate_source_record_id"):
            prev = room["_duplicate_source_record_id"]
            findings.append(_normalize_finding(
                "DUPLICATE_SOURCE_RECORD_ID",
                "ERROR",
                "room",
                [prev["canonical_id"], room["_canonical_id"]],
                f"Two rooms share source_record_id={src!r}: canonical IDs "
                f"{prev['canonical_id']!r} and {room['_canonical_id']!r}. "
                f"This creates ambiguous identity across reorder/rebuild.",
            ))
        req = room["_identity"].get("requirement_id")
        if req and room.get("_duplicate_requirement_id"):
            prev = room["_duplicate_requirement_id"]
            findings.append(_normalize_finding(
                "DUPLICATE_REQUIREMENT_ID",
                "ERROR",
                "room",
                [prev["canonical_id"], room["_canonical_id"]],
                f"Two rooms share requirement_id={req!r}: canonical IDs "
                f"{prev['canonical_id']!r} and {room['_canonical_id']!r}.",
            ))
        lin = room["_identity"].get("creation_lineage")
        if lin and room.get("_duplicate_creation_lineage"):
            prev = room["_duplicate_creation_lineage"]
            findings.append(_normalize_finding(
                "DUPLICATE_CREATION_LINEAGE",
                "ERROR",
                "room",
                [prev["canonical_id"], room["_canonical_id"]],
                f"Two rooms share creation_lineage={lin!r}: canonical IDs "
                f"{prev['canonical_id']!r} and {room['_canonical_id']!r}.",
            ))

        canonical_id = room["_canonical_id"]
        rect = _rect_from_polygon(poly)
        poly_area = round(_polygon_area(poly), 2)
        compat = _bbox_compatibility(poly, rect)
        original_area = _f(room.get("area_sqft", 0))

        # Emit LEGACY_AREA_MISMATCH when input area differs from polygon area
        if original_area > _EPS and abs(poly_area - original_area) > _EPS:
            findings.append(_normalize_finding(
                "LEGACY_AREA_MISMATCH",
                "WARNING",
                "room",
                [room.get("id", "?")],
                f"Room {room.get('id', '?')}: polygon area ({poly_area} sq ft) "
                f"differs from input area_sqft ({original_area} sq ft). "
                f"Polygon-derived area is authoritative.",
            ))

        # Emit APPROXIMATE_COMPATIBILITY_GEOMETRY for non-rectangular polygons
        if not compat["exact_representation"]:
            findings.append(_normalize_finding(
                "APPROXIMATE_COMPATIBILITY_GEOMETRY",
                "WARNING",
                "room",
                [room.get("id", "?")],
                f"Room {room.get('id', '?')}: bounding box is approximate; "
                f"exact IFC/Revit issue is blocked for this room.",
            ))

        st_id = _storey_id_for_room(room)
        fl = int(room.get("floor", 0))
        lv_id = _deterministic_id("level", st_id)
        # Find matching level
        for lv in levels:
            if st_id in lv["storey_ids"]:
                lv_id = lv["id"]
                break

        canonical_rooms.append({
            "id": canonical_id,
            "identity": dict(room["_identity"]),
            "geometry_revision": room.get("geometry_revision", 1),
            "polygon": [list(p) for p in poly],
            "storey_id": st_id,
            "level_id": lv_id,
            "area_sqft": poly_area,  # DERIVED FROM POLYGON, not from input
            "legacy_metadata": {
                "original_area_sqft": round(original_area, 2),
                "source_record_id": room.get("source_record_id"),
                "requirement_id": room.get("requirement_id") or room.get("req_id"),
                "creation_lineage": room.get("creation_lineage"),
            },
            "compatibility": {
                "bbox": {
                    **rect,
                    "derived": True,
                    "exact_representation": compat["exact_representation"],
                }
            },
            "zone": room.get("placement_zone") or room.get("vastu_zone", ""),
            "type": room.get("type", ""),
            "realm": room.get("realm", ""),
            "floor": fl,
            "description": room.get("description", ""),
            "accessibility": room.get("accessibility", ""),
            "flooring_type": room.get("flooring_type", ""),
            "wall_finish": room.get("wall_finish", ""),
            "ceiling_height": room.get("ceiling_height", ""),
            "lighting_type": room.get("lighting_type", ""),
            "hvac_type": room.get("hvac_type", ""),
            "ev_charging": room.get("ev_charging", ""),
            "staff_beds": room.get("staff_beds", ""),
            "kitchenette": room.get("kitchenette", False),
            # ---- x/y/w/h REMOVED: polygon is the sole authoritative geometry.
            # Compatibility bbox lives in compatibility.bbox and is derived
            # from polygon, NOT independently editable.  Legacy consumers that
            # need rect coordinates must read compatibility.bbox or polygon.
            # ---- Legacy metadata passthrough for downstream consumers ----
            "suite_group": room.get("suite_group"),
            "windows": room.get("windows", []),
            "door": room.get("door"),
            "alignment_status": room.get("alignment_status", "unchecked"),
            "alignment_rule_applied": room.get("alignment_rule_applied"),
            "alignment_status_mode": room.get("alignment_status_mode"),
            "official_rule_available": room.get("official_rule_available", False),
            "placement_zone": room.get("placement_zone"),
            "vastu_zone": room.get("vastu_zone"),
            "ventilation_met": room.get("ventilation_met"),
            "compromise_reason": room.get("compromise_reason"),
            "remedy": room.get("remedy"),
            "micro_vastu": room.get("micro_vastu", {}),
            "furniture": room.get("furniture", []),
        })

    # Invariant: all room IDs must be unique.
    # Collision resolution is order-independent: rooms with the same ID are
    # sorted by stable fingerprint (type, floor, zone, centroid), then
    # assigned deterministic suffixes (_1, _2, ...) in sorted order.
    # The first room in sorted order keeps the original ID.
    def _room_fingerprint(room: dict, poly: list[tuple[float, float]]) -> tuple:
        cx = round(sum(p[0] for p in poly) / len(poly), 4) if poly else 0.0
        cy = round(sum(p[1] for p in poly) / len(poly), 4) if poly else 0.0
        return (
            room.get("type", ""),
            room.get("floor", 0),
            room.get("placement_zone") or room.get("vastu_zone", ""),
            cx, cy,
        )

    _id_groups: dict[str, list[tuple[tuple, int]]] = defaultdict(list)
    for idx, r in enumerate(canonical_rooms):
        fp = _room_fingerprint(rooms[idx], r["polygon"])
        _id_groups[r["id"]].append((fp, idx))

    collision_metadata = []
    for rid, entries in _id_groups.items():
        if len(entries) <= 1:
            continue
        entries.sort(key=lambda e: e[0])  # order-independent
        for seq, (fp, idx) in enumerate(entries):
            if seq == 0:
                continue
            new_id = f"{rid}_{seq}"
            canonical_rooms[idx]["id"] = new_id
            collision_metadata.append({
                "original_id": rid,
                "resolved_id": new_id,
                "room_index": idx,
                "fingerprint": fp,
                "sequence": seq,
            })
            findings.append(_normalize_finding(
                "DUPLICATE_ROOM_ID",
                "ERROR",
                "room",
                [rid],
                f"Room ID '{rid}' collision resolved: index {idx} → '{new_id}' "
                f"(fingerprint: {fp}).",
            ))

    # Validation — merge conflict findings (from room normalization) with
    # structural validation findings (from walls, doors, etc.).
    structural_findings = _validate_model(canonical_rooms, walls, doors, windows, boundary_segments)
    findings.extend(structural_findings)

    model_out = {
        "schema_version": "1.0",
        "units": "feet",
        "coordinate_system": {"origin": [0.0, 0.0], "y_up": True},
        "geometry_authority": "CANONICAL_POLYGON",
        "canonical_created_at_stage": "build_canonical_model",
        "legacy_adapter_used": False,
        "building": {
            "id": _deterministic_id("building", str(plot_w), str(plot_d)),
            "name": project_state.get("user_prompt", "Unknown")[:50],
        },
        # ---- Plot block (added to prevent downstream plot-loss bug) ----
        # Preserves all input plot fields and adds normalized width/depth/unit/
        # boundary_polygon aliases.  An existing boundary_polygon is preserved.
        "plot": {
            **dict(plot),
            "width": _f(plot.get("usable_width_ft") or plot.get("width_ft") or plot.get("width")),
            "depth": _f(plot.get("usable_depth_ft") or plot.get("depth_ft") or plot.get("depth")),
            "unit": plot.get("unit", "ft"),
            "boundary_polygon": [
                [0.0, 0.0],
                [float(_f(plot.get("usable_width_ft") or plot.get("width_ft") or plot.get("width", 0))), 0.0],
                [float(_f(plot.get("usable_width_ft") or plot.get("width_ft") or plot.get("width", 0))),
                 float(_f(plot.get("usable_depth_ft") or plot.get("depth_ft") or plot.get("depth", 0)))],
                [0.0, float(_f(plot.get("usable_depth_ft") or plot.get("depth_ft") or plot.get("depth", 0)))],
            ],
        },
        # Preserve canonical_created_at_stage if it was already set on the
        # input project_state (e.g., by create_canonical_spatial_plan or
        # legacy_state_to_canonical_model).  This avoids silently overwriting
        # the legitimate creation stage just because build_canonical_model ran.
        "_pre_existing_stage": project_state.get("canonical_created_at_stage"),
        "storeys": storeys,
        "levels": levels,
        "rooms": canonical_rooms,
        "boundary_segments": boundary_segments,
        "walls": walls,
        "doors": doors,
        "windows": windows,
        "slabs": slabs,
        "shafts": shafts,
        "stairs": stairs,
        "stable_ids": True,
        "generation_metadata": {
            "timestamp": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
            "source": "build_canonical_model",
            "revision": 1,
            "collision_resolution": collision_metadata,
            "geometry_authority": "CANONICAL_POLYGON",
            "canonical_created_at_stage": "GEOMETRY_MODEL_BUILDER",
            "legacy_adapter_used": False,
            "schema_version": "2.0",
        },
        "geometry_valid": len([
            f for f in findings
            if f.get("severity") in ("ERROR", "CRITICAL")
            and f.get("blocks_geometry", False)
        ]) == 0,
        "validation_findings": findings,
    }
    return model_out


# ---------------------------------------------------------------------------
# Legacy compatibility adapter
# ---------------------------------------------------------------------------

def canonical_room_to_legacy_rect(room: dict) -> dict:
    """Convert a canonical room to a legacy x/y/w/h rectangle for
    unmigrated consumers.

    Returns ``{x, y, w, h, derived, exact_representation}`` where:
    - ``x/y/w/h`` come from the compatibility.bbox (derived from polygon).
    - ``derived`` is always ``True`` — these values are derived, not authored.
    - ``exact_representation`` is ``True`` when the polygon is a rectangle,
      ``False`` otherwise.

    No module may calculate its own bounding rectangle.  All rectangle
    requests must flow through this adapter.
    """
    compat_bbox = room.get("compatibility", {}).get("bbox", {})
    if compat_bbox:
        return {
            "x": _f(compat_bbox.get("x", 0)),
            "y": _f(compat_bbox.get("y", 0)),
            "w": _f(compat_bbox.get("w", 0)),
            "h": _f(compat_bbox.get("h", 0)),
            "derived": True,
            "exact_representation": bool(compat_bbox.get("exact_representation", False)),
        }
    # Fallback: derive bbox from polygon directly
    poly = room.get("polygon", [])
    if len(poly) >= 3:
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        return {
            "x": min(xs), "y": min(ys),
            "w": max(xs) - min(xs), "h": max(ys) - min(ys),
            "derived": True,
            "exact_representation": False,
        }
    return {"x": 0, "y": 0, "w": 0, "h": 0, "derived": True, "exact_representation": False}


def legacy_state_to_canonical_model(project_state: dict) -> dict:
    """Migrate a legacy project_state (x/y/w/h rooms) to canonical model.

    This function:
    1. Generates polygon vertices from each room's x/y/w/h.
    2. Calls build_canonical_model to create the canonical structure.
    3. Sets model-level metadata to indicate legacy migration.

    New projects must use create_canonical_spatial_plan() instead.
    """
    rooms = project_state.get("rooms", [])
    plot = project_state.get("plot", {})

    # Convert x/y/w/h to polygon for each room
    migrated_rooms = []
    for room in rooms:
        migrated = dict(room)
        if "polygon" not in migrated and "x" in migrated and "w" in migrated:
            x, y, w, h = _f(migrated["x"]), _f(migrated["y"]), _f(migrated["w"]), _f(migrated["h"])
            migrated["polygon"] = [
                [x, y], [x + w, y], [x + w, y + h], [x, y + h]
            ]
        migrated_rooms.append(migrated)

    migrated_state = dict(project_state)
    migrated_state["rooms"] = migrated_rooms

    model = build_canonical_model(migrated_state)
    model["geometry_authority"] = "CANONICAL_POLYGON"
    model["canonical_created_at_stage"] = "LEGACY_MIGRATION"
    model["legacy_adapter_used"] = True
    if "generation_metadata" not in model:
        model["generation_metadata"] = {}
    model["generation_metadata"]["legacy_migration"] = True
    model["generation_metadata"]["source"] = "legacy_state_to_canonical_model"
    return model


# ---------------------------------------------------------------------------
# Geometry revision and change log helpers
# ---------------------------------------------------------------------------

def _increment_revision(room_or_revision, change_type=None, reason=None,
                         actor: str = "geometry_solver",
                         change_log: list | None = None) -> int:
    """Increment a room's geometry_revision and record the change.

    Returns the new revision number.

    Supports two calling conventions (backward compatible):
    - ``_increment_revision(room_dict, change_type, reason, ...)`` — increment
      the room's revision and append a change-log entry.
    - ``_increment_revision(n)`` — pure arithmetic helper for tests: returns
      ``n + 1`` without touching any dict or change log.
    """
    # Pure-arithmetic mode (used by tests): a single positional int.
    if isinstance(room_or_revision, int) and change_type is None and reason is None:
        return room_or_revision + 1

    # Standard mode: operate on a room dict.
    room = room_or_revision
    before = room.get("geometry_revision", 1)
    after = before + 1
    room["geometry_revision"] = after

    if change_log is not None:
        change_log.append({
            "change_id": f"geom_change_{len(change_log) + 1:03d}",
            "entity_id": room.get("id", "unknown"),
            "revision_before": before,
            "revision_after": after,
            "change_type": change_type,
            "reason": reason,
            "actor": actor,
            "before_polygon": [list(p) for p in room.get("_prev_polygon", room.get("polygon", []))],
            "after_polygon": [list(p) for p in room.get("polygon", [])],
            "timestamp": None,
        })
    return after


def _set_polygon(room: dict, new_polygon: list, change_type: str, reason: str,
                  actor: str = "geometry_solver", change_log: list | None = None) -> None:
    """Set a room's polygon and increment revision if geometry changed."""
    old_polygon = room.get("polygon", [])
    # Store previous polygon for change log
    room["_prev_polygon"] = old_polygon
    room["polygon"] = [list(p) for p in new_polygon]
    # Recalculate area from polygon
    room["area_sqft"] = round(_polygon_area(new_polygon), 2)
    # Recalculate compatibility bbox
    rect = _rect_from_polygon(new_polygon)
    compat = _bbox_compatibility(new_polygon, rect)
    if "compatibility" not in room:
        room["compatibility"] = {}
    room["compatibility"]["bbox"] = {
        **rect,
        "derived": True,
        "exact_representation": compat["exact_representation"],
    }
    # Increment revision if polygon actually changed
    if _polygons_differ(old_polygon, new_polygon):
        _increment_revision(room, change_type, reason, actor, change_log)


def _polygons_differ(a: list, b: list) -> bool:
    """Check if two polygon vertex lists represent different geometry."""
    if len(a) != len(b):
        return True
    for pa, pb in zip(a, b):
        if abs(pa[0] - pb[0]) > 1e-6 or abs(pa[1] - pb[1]) > 1e-6:
            return True
    return False


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

def _validate_model(
    rooms: list[dict],
    walls: list[dict],
    doors: list[dict],
    windows: list[dict],
    boundary_segments: list[dict],
) -> list[dict]:
    """Run canonical model invariants."""
    findings: list[dict] = []

    room_ids = {r["id"] for r in rooms}
    wall_ids = {w["id"] for w in walls}

    # Invariant 3: every room belongs to a storey and level
    for r in rooms:
        if not r.get("storey_id"):
            findings.append(_normalize_finding("MISSING_STOREY", "ERROR", "room", [r["id"]], f"Room {r['id']} has no storey_id."))
        if not r.get("level_id"):
            findings.append(_normalize_finding("MISSING_LEVEL", "ERROR", "room", [r["id"]], f"Room {r['id']} has no level_id."))

    # Invariant 4: no duplicate canonical_ids
    canonical_ids: list = [r.get("canonical_id") for r in rooms if r.get("canonical_id")]
    seen_canonical: dict = {}
    for cid in canonical_ids:
        if cid in seen_canonical:
            findings.append(_normalize_finding(
                "DUPLICATE_CANONICAL_ID", "ERROR", "room",
                [cid], f"Duplicate canonical_id: {cid!r} appears more than once.",
            ))
        seen_canonical[cid] = True

    # Invariant 5: no duplicate source_record_id (stable provenance)
    src_ids: list = [r.get("identity", {}).get("source_record_id") for r in rooms if r.get("identity", {}).get("source_record_id")]
    seen_src: dict = {}
    for sid in src_ids:
        if sid in seen_src:
            findings.append(_normalize_finding(
                "DUPLICATE_SOURCE_RECORD_ID", "ERROR", "room",
                [sid], f"Duplicate source_record_id: {sid!r} appears more than once.",
            ))
        seen_src[sid] = True

    # Invariant 6: polygon validity (self-intersection check)
    for r in rooms:
        poly = r.get("polygon")
        if poly and len(poly) >= 3:
            if not _is_simple_polygon([(float(v[0]), float(v[1])) for v in poly]):
                findings.append(_normalize_finding(
                    "INVALID_POLYGON", "ERROR", "room", [r["id"]],
                    f"Room {r['id']}: polygon is self-intersecting or degenerate.",
                ))

    # Invariant 7: doors/windows reference a valid wall
    for d in doors:
        if d.get("host_wall_id") not in wall_ids:
            findings.append(_normalize_finding("ORPHAN_DOOR", "ERROR", "door", [d["id"]], f"Door {d['id']} references missing wall {d.get('host_wall_id')}."))

    for w in windows:
        if w.get("host_wall_id") not in wall_ids:
            findings.append(_normalize_finding("ORPHAN_WINDOW", "ERROR", "window", [w["id"]], f"Window {w['id']} references missing wall {w.get('host_wall_id')}."))

    # Invariant 5: walls have stable IDs and thickness
    for wall in walls:
        if not wall.get("id"):
            findings.append(_normalize_finding("WALL_MISSING_ID", "ERROR", "wall", [], "A wall has no id."))
        if wall.get("thickness_ft", 0) <= 0:
            findings.append(_normalize_finding("WALL_ZERO_THICKNESS", "ERROR", "wall", [wall.get("id", "?")], f"Wall {wall.get('id', '?')} has zero or negative thickness."))

    return findings  # stacking validation moved to separate task


def validate_canonical_model(model: dict) -> dict:
    findings = _validate_model(
        model.get("rooms", []),
        model.get("walls", []),
        model.get("doors", []),
        model.get("windows", []),
        model.get("boundary_segments", []),
    )
    findings = findings + model.get("validation_findings", [])
    blocking = [f for f in findings if is_geometry_blocking(f)]
    return {
        "geometry_valid": len(blocking) == 0,
        "validation_findings": findings,
        "error_count": len([f for f in findings if f["severity"] == "ERROR"]),
        "warning_count": len([f for f in findings if f["severity"] == "WARNING"]),
    }


def validate_model(model: dict) -> dict:
    """Run geometry validation on a canonical model using the geometry solver.

    Returns a dict with keys: overlaps, clearance_violations,
    minimum_size_violations, brahmasthan_clear, geometry_valid,
    validation_findings, error_count, warning_count
    """
    from .geometry_solver import check_brahmasthan, check_clearances, check_minimum_sizes, check_overlaps
    rooms = model.get("rooms", [])
    building = model.get("building", {})
    overlaps = check_overlaps(rooms)
    clearance_violations = check_clearances(rooms)
    size_violations = check_minimum_sizes(rooms)
    brahmasthan_clear = check_brahmasthan(rooms, building.get("width_ft", 0), building.get("depth_ft", 0))

    # Solver geometry validity
    solver_valid = not overlaps and not clearance_violations and not size_violations and brahmasthan_clear

    # Canonical model structural validation (walls, doors, storeys, etc.)
    canonical_findings = _validate_model(
        model.get("rooms", []),
        model.get("walls", []),
        model.get("doors", []),
        model.get("windows", []),
        model.get("boundary_segments", []),
    )

    # Include any findings already stored in the model (e.g., duplicate-room-ID
    # findings generated during build_canonical_model).
    canonical_findings = canonical_findings + model.get("validation_findings", [])

    # Merge solver geometry findings so that geometry_valid=False implies
    # validation_findings is non-empty.
    for ov in overlaps:
        canonical_findings.append(_normalize_finding(
            "ROOM_OVERLAP", "error", "room",
            [ov.get("room_a", ""), ov.get("room_b", "")],
            f"Rooms {ov.get('room_a')} and {ov.get('room_b')} overlap by {ov.get('overlap_area', 0)} sq ft.",
        ))
    for cv in clearance_violations:
        canonical_findings.append(_normalize_finding(
            "CLEARANCE_VIOLATION", "error", "room",
            [cv.get("room_a", ""), cv.get("room_b", "")],
            f"Rooms {cv.get('room_a')} and {cv.get('room_b')} have {cv.get('clearance_ft', 0)} ft clearance (minimum {cv.get('minimum_clearance_ft', 0)} ft).",
        ))
    for sv in size_violations:
        canonical_findings.append(_normalize_finding(
            "MINIMUM_SIZE_VIOLATION", "warning", "room",
            [sv.get("room_id", "")],
            f"Room {sv.get('room_id')} is below minimum size ({sv.get('actual_sqft', 0)} vs {sv.get('minimum_sqft', 0)} sq ft).",
        ))
    if not brahmasthan_clear:
        canonical_findings.append(_normalize_finding(
            "BRAHMAS_THAN_VIOLATION", "warning", "room", [],
            "One or more rooms overlap the Brahmasthan zone.",
        ))

    errors = [f for f in canonical_findings if f["severity"] == "ERROR"]
    canonical_valid = len(errors) == 0

    return {
        "overlaps": overlaps,
        "clearance_violations": clearance_violations,
        "minimum_size_violations": size_violations,
        "brahmasthan_clear": brahmasthan_clear,
        "geometry_valid": solver_valid and canonical_valid,
        "validation_findings": canonical_findings,
        "error_count": len(errors),
        "warning_count": len([f for f in canonical_findings if f["severity"] == "warning"]),
    }


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def model_to_json(model: dict) -> str:
    """Serialize model to JSON string."""
    import json
    return json.dumps(model, indent=2, default=str)


def json_to_model(text: str) -> dict:
    """Deserialize model from JSON string."""
    import json
    return json.loads(text)


def round_trip(model: dict) -> dict:
    """Write → read → return.  Verifies stable IDs survive serialization."""
    text = model_to_json(model)
    restored = json_to_model(text)
    # Verify wall IDs preserved
    orig_ids = {w["id"] for w in model.get("walls", [])}
    restored_ids = {w["id"] for w in restored.get("walls", [])}
    if orig_ids != restored_ids:
        raise AssertionError(
            f"Wall IDs changed during round-trip: {orig_ids ^ restored_ids}"
        )
    return restored


def write_canonical_model(
    project_state: dict,
    building_codes: dict | None = None,
) -> dict:
    """Build the canonical model and stamp it into project_state."""
    model = build_canonical_model(project_state, building_codes)
    project_state["geometry_model"] = model
    return model


def read_canonical_model(project_state: dict) -> dict | None:
    """Return the canonical model from project_state, or None if absent."""
    return project_state.get("geometry_model")
