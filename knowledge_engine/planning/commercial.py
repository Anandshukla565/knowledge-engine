"""Commercial space room type definitions and adjacency rules.

Provides room size tables, adjacency preferences, and zone mappings for
commercial_office, commercial_retail, and restaurant space types.
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Commercial room size table (feet)
# ---------------------------------------------------------------------------

COMMERCIAL_ROOM_SIZES: dict[str, dict] = {
    # --- Office ---
    "Office":              {"w": 12.0, "h": 10.0, "area": 120.0},
    "Private Office":      {"w": 14.0, "h": 12.0, "area": 168.0},
    "Manager Office":      {"w": 16.0, "h": 14.0, "area": 224.0},
    "Conference Room":     {"w": 24.0, "h": 20.0, "area": 480.0},
    "Meeting Room":        {"w": 14.0, "h": 12.0, "area": 168.0},
    "Reception":           {"w": 18.0, "h": 14.0, "area": 252.0},
    "Break Room":          {"w": 16.0, "h": 14.0, "area": 224.0},
    "Pantry":              {"w": 10.0, "h": 10.0, "area": 100.0},
    "Restroom":            {"w": 10.0, "h": 10.0, "area": 100.0},
    "Server Room":         {"w": 12.0, "h": 12.0, "area": 144.0},
    "Storage":             {"w": 12.0, "h": 14.0, "area": 168.0},
    "File Room":           {"w": 10.0, "h": 10.0, "area": 100.0},
    "Lobby":               {"w": 20.0, "h": 16.0, "area": 320.0},
    "Copy Room":           {"w": 8.0,  "h": 8.0,  "area": 64.0},
    # --- Retail ---
    "Retail Floor":        {"w": 40.0, "h": 30.0, "area": 1200.0},
    "Stock Room":          {"w": 14.0, "h": 16.0, "area": 224.0},
    "Checkout Counter":    {"w": 10.0, "h": 8.0,  "area": 80.0},
    "Fitting Room":        {"w": 8.0,  "h": 8.0,  "area": 64.0},
    "Display Area":        {"w": 20.0, "h": 16.0, "area": 320.0},
    "Customer Service":    {"w": 12.0, "h": 10.0, "area": 120.0},
    # --- Restaurant ---
    "Dining Area":         {"w": 30.0, "h": 24.0, "area": 720.0},
    "Kitchen":             {"w": 20.0, "h": 16.0, "area": 320.0},
    "Prep Area":           {"w": 12.0, "h": 10.0, "area": 120.0},
    "Bar":                 {"w": 14.0, "h": 10.0, "area": 140.0},
    "Wash Station":        {"w": 8.0,  "h": 8.0,  "area": 64.0},
    "Cold Storage":        {"w": 10.0, "h": 12.0, "area": 120.0},
    "Wait Station":        {"w": 8.0,  "h": 6.0,  "area": 48.0},
    "Takeaway Counter":    {"w": 12.0, "h": 8.0,  "area": 96.0},
    # --- Common / Mixed-use ---
    "Elevator Lobby":      {"w": 12.0, "h": 10.0, "area": 120.0},
    "Corridor":            {"w": 8.0,  "h": 4.0,  "area": 32.0},
    "Parking":             {"w": 24.0, "h": 48.0, "area": 1152.0},
    "Utility":             {"w": 8.0,  "h": 10.0, "area": 80.0},
    "Security":            {"w": 8.0,  "h": 8.0,  "area": 64.0},
}

# ---------------------------------------------------------------------------
# Commercial space_type definitions
# ---------------------------------------------------------------------------

VALID_SPACE_TYPES = (
    "residential",
    "commercial_office",
    "commercial_retail",
    "restaurant",
    "mixed_use",
)

# Which space types are considered commercial (non-residential)
COMMERCIAL_SPACE_TYPES = (
    "commercial_office",
    "commercial_retail",
    "restaurant",
    "mixed_use",
)


def is_commercial_space_type(space_type: str) -> bool:
    return space_type in COMMERCIAL_SPACE_TYPES


# ---------------------------------------------------------------------------
# Per-space-type mandatory rooms
# ---------------------------------------------------------------------------

SPACE_TYPE_REQUIRED_ROOMS: dict[str, list[str]] = {
    "commercial_office": [
        "Office", "Reception", "Conference Room", "Restroom",
    ],
    "commercial_retail": [
        "Retail Floor", "Checkout Counter", "Stock Room", "Restroom",
    ],
    "restaurant": [
        "Dining Area", "Kitchen", "Restroom",
    ],
    "mixed_use": [
        "Office", "Retail Floor", "Restroom",
    ],
    "residential": [],  # handled by existing residential logic
}


def get_required_rooms(space_type: str) -> list[str]:
    return list(SPACE_TYPE_REQUIRED_ROOMS.get(space_type, []))


# ---------------------------------------------------------------------------
# Commercial adjacency preferences: room_type -> list of preferred neighbors
# ---------------------------------------------------------------------------

COMMERCIAL_ADJACENCY_RULES: dict[str, list[str]] = {
    # Office
    "Office":           ["Reception", "Lobby", "Elevator Lobby"],
    "Private Office":   ["Office", "Conference Room", "Manager Office"],
    "Manager Office":   ["Office", "Conference Room"],
    "Conference Room":  ["Office", "Manager Office"],
    "Meeting Room":     ["Office", "Conference Room"],
    "Reception":      ["Lobby", "Elevator Lobby", "Office"],
    "Break Room":      ["Pantry", "Restroom"],
    "Server Room":     ["Storage", "Utility"],
    "File Room":       ["Office", "Private Office"],
    "Copy Room":       ["Office"],
    # Retail
    "Retail Floor":         ["Checkout Counter", "Display Area", "Stock Room"],
    "Retail Floor":    ["Checkout Counter", "Display Area", "Stock Room"],
    "Checkout Counter":   ["Retail Floor", "Display Area"],
    "Stock Room":         ["Retail Floor", "Storage"],
    "Fitting Room":       ["Retail Floor", "Restroom"],
    "Display Area":       ["Retail Floor"],
    "Customer Service":   ["Reception", "Retail Floor"],
    # Restaurant
    "Kitchen":             ["Prep Area", "Cold Storage", "Dining Area"],
    "Dining Area":         ["Kitchen", "Prep Area", "Bar", "Wait Station"],
    "Prep Area":      ["Kitchen", "Cold Storage"],
    "Bar":            ["Dining Area", "Prep Area"],
    "Wash Station":   ["Kitchen", "Dining Area"],
    "Cold Storage":   ["Kitchen", "Prep Area"],
    "Wait Station":   ["Dining Area", "Takeaway Counter"],
    "Takeaway Counter": ["Dining Area", "Reception"],
    # Common / Mixed-use
    "Restroom":       ["Lobby", "Reception", "Dining Area", "Retail Floor"],
    "Elevator Lobby": ["Reception", "Lobby", "Office"],
    "Lobby":          ["Reception", "Elevator Lobby"],
    "Pantry":         ["Break Room", "Kitchen"],
    "Parking":        ["Elevator Lobby", "Lobby"],
    "Corridor":       ["Lobby", "Elevator Lobby", "Office", "Retail Floor"],
    "Utility":        ["Storage", "Restroom"],
    "Security":       ["Reception", "Lobby"],
    "Storage":        ["Stock Room", "Server Room"],
}

# ---------------------------------------------------------------------------
# Vastu zone preferences for commercial rooms
# ---------------------------------------------------------------------------

COMMERCIAL_VASTU_ZONE_PREFS: dict[str, list[str]] = {
    "Reception":       ["NE", "N", "C"],
    "Office":          ["NW", "W", "C"],
    "Manager Office":  ["SW", "W", "C"],
    "Conference Room": ["NW", "N", "C"],
    "Meeting Room":    ["NW", "N", "C"],
    "Private Office":  ["SW", "W", "C"],
    "Retail Floor":    ["NE", "N", "C"],
    "Display Area":    ["NE", "N"],
    "Checkout Counter": ["NE", "N"],
    "Stock Room":      ["SW", "S", "SE"],
    "Dining Area":     ["W", "SW", "S"],
    "Kitchen":         ["SE", "S"],
    "Prep Area":       ["SE", "S"],
    "Bar":             ["SE", "S", "W"],
    "Lobby":           ["NE", "N", "C"],
    "Elevator Lobby":  ["C", "S"],
    "Restroom":        ["NW", "W", "SE"],
    "Break Room":      ["NW", "W"],
    "Pantry":          ["SE", "S"],
    "Server Room":     ["SW", "S", "SE"],
    "Cold Storage":    ["SE", "S"],
    "Parking":         ["S", "SE", "SW"],
    "Storage":         ["SE", "S", "SW"],
    "Fitting Room":    ["NW", "W"],
    "Customer Service": ["NE", "N"],
    "Wash Station":    ["SE", "S"],
    "Takeaway Counter": ["NE", "N"],
    "Wait Station":    ["NE", "N"],
    "File Room":       ["SW", "W"],
    "Copy Room":       ["NW", "W"],
    "Utility":         ["SE", "S"],
    "Security":        ["NE", "N", "C"],
}


def vastu_pref_for_commercial(room_type: str) -> list[str]:
    """Return preferred Vastu zones for a commercial room type."""
    return COMMERCIAL_VASTU_ZONE_PREFS.get(room_type, ["C"])


# ---------------------------------------------------------------------------
# Commercial room default placement preferences (relative to plot)
# ---------------------------------------------------------------------------

COMMERCIAL_PLACEMENT_PREFS: dict[str, list] = {
    "Reception":        [(0.0, 0.0)],
    "Lobby":            [(0.0, 0.0)],
    "Elevator Lobby":   [(0.0, 0.0)],
    "Retail Floor":     [(0.0, 0.0)],
    "Display Area":     [(0.0, 0.0)],
    "Checkout Counter": [(0.0, 0.0)],
    "Takeaway Counter": [(0.0, 0.0)],
    "Dining Area":      [(0.0, 0.0)],
    "Parking":          [(0.0, 0.0)],
    "Security":         [(0.0, 0.0)],
    "Customer Service": [(0.0, 0.0)],
    # Default: place towards back / right side
    "Office":           [(0.0, 0.0)],
    "Private Office":   [(0.0, 0.0)],
    "Manager Office":   [(0.0, 0.0)],
    "Conference Room":  [(0.0, 0.0)],
    "Meeting Room":     [(0.0, 0.0)],
    "Break Room":       [(0.0, 0.0)],
    "Kitchen":          [(0.0, 0.0)],
    "Prep Area":        [(0.0, 0.0)],
    "Bar":              [(0.0, 0.0)],
    "Wash Station":     [(0.0, 0.0)],
    "Cold Storage":     [(0.0, 0.0)],
    "Wait Station":     [(0.0, 0.0)],
    "Restroom":         [(0.0, 0.0)],
    "Server Room":      [(0.0, 0.0)],
    "Storage":          [(0.0, 0.0)],
    "Stock Room":       [(0.0, 0.0)],
    "Fitting Room":     [(0.0, 0.0)],
    "Pantry":           [(0.0, 0.0)],
    "File Room":        [(0.0, 0.0)],
    "Copy Room":        [(0.0, 0.0)],
    "Utility":          [(0.0, 0.0)],
    "Corridor":         [(0.0, 0.0)],
}


def placement_pref_for(room_type: str) -> list:
    return list(COMMERCIAL_PLACEMENT_PREFS.get(room_type, [(0.0, 0.0)]))


# ---------------------------------------------------------------------------
# Build commercial floor spec
# ---------------------------------------------------------------------------

def build_commercial_floor_spec(space_type: str) -> list[tuple]:
    """Return list of (room_type, width, height) tuples for the given space type.

    Each tuple is (type, base_w, base_h) — dimensions from COMMERCIAL_ROOM_SIZES.
    """
    space_type = space_type.lower()
    if space_type == "commercial_office":
        return [
            ("Reception",       18.0, 14.0),
            ("Lobby",           20.0, 16.0),
            ("Elevator Lobby",  12.0, 10.0),
            ("Conference Room", 24.0, 20.0),
            ("Meeting Room",    14.0, 12.0),
            ("Private Office",  14.0, 12.0),
            ("Office",          12.0, 10.0),
            ("Manager Office",  16.0, 14.0),
            ("Break Room",      16.0, 14.0),
            ("Pantry",          10.0, 10.0),
            ("Restroom",        10.0, 10.0),
            ("Server Room",     12.0, 12.0),
            ("Storage",         12.0, 14.0),
            ("File Room",       10.0, 10.0),
            ("Copy Room",       8.0,  8.0),
            ("Utility",         8.0,  10.0),
            ("Security",        8.0,  8.0),
            ("Corridor",        8.0,  4.0),
        ]
    elif space_type == "commercial_retail":
        return [
            ("Reception",        18.0, 14.0),
            ("Retail Floor",     40.0, 30.0),
            ("Display Area",     20.0, 16.0),
            ("Checkout Counter", 10.0, 8.0),
            ("Stock Room",       14.0, 16.0),
            ("Storage",          12.0, 14.0),
            ("Fitting Room",     8.0,  8.0),
            ("Customer Service", 12.0, 10.0),
            ("Restroom",         10.0, 10.0),
            ("Utility",          8.0,  10.0),
            ("Security",         8.0,  8.0),
            ("Corridor",         8.0,  4.0),
        ]
    elif space_type == "restaurant":
        return [
            ("Reception",       16.0, 12.0),
            ("Dining Area",     30.0, 24.0),
            ("Kitchen",         20.0, 16.0),
            ("Prep Area",       12.0, 10.0),
            ("Bar",             14.0, 10.0),
            ("Wash Station",    8.0,  8.0),
            ("Cold Storage",    10.0, 12.0),
            ("Wait Station",    8.0,  6.0),
            ("Takeaway Counter",12.0, 8.0),
            ("Restroom",        10.0, 10.0),
            ("Storage",         10.0, 12.0),
            ("Utility",         8.0,  10.0),
            ("Security",        8.0,  8.0),
            ("Corridor",        8.0,  4.0),
        ]
    elif space_type == "mixed_use":
        return [
            ("Reception",       16.0, 12.0),
            ("Retail Floor",    30.0, 20.0),
            ("Office",          12.0, 10.0),
            ("Private Office",  14.0, 12.0),
            ("Restroom",        10.0, 10.0),
            ("Storage",         12.0, 14.0),
            ("Utility",         8.0,  10.0),
            ("Security",        8.0,  8.0),
            ("Corridor",        8.0,  4.0),
        ]
    return []
