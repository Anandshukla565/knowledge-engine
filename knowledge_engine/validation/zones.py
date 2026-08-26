from __future__ import annotations

from .rectangles import Rect


def brahmasthan_bounds(plot_width_ft: float, plot_depth_ft: float) -> Rect:
    center_width = plot_width_ft / 3.0
    center_depth = plot_depth_ft / 3.0
    return Rect(
        x=center_width,
        y=center_depth,
        width=center_width,
        height=center_depth,
    )


def classify_room_zone(plot_width_ft: float, plot_depth_ft: float, room_rect: Rect) -> str:
    center_x = room_rect.x + (room_rect.width / 2.0)
    center_y = room_rect.y + (room_rect.height / 2.0)
    horizontal = "center"
    vertical = "center"
    if center_x < plot_width_ft / 3.0:
        horizontal = "west"
    elif center_x > (plot_width_ft * 2.0) / 3.0:
        horizontal = "east"
    if center_y < plot_depth_ft / 3.0:
        vertical = "south"
    elif center_y > (plot_depth_ft * 2.0) / 3.0:
        vertical = "north"
    if horizontal == "center" and vertical == "center":
        return "center"
    if horizontal == "center":
        return vertical
    if vertical == "center":
        return horizontal
    return f"{vertical}_{horizontal}"
