from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def left(self) -> float:
        return self.x

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y

    @property
    def top(self) -> float:
        return self.y + self.height

    @property
    def area(self) -> float:
        return round(self.width * self.height, 2)

    def is_inside(self, outer: "Rect") -> bool:
        return (
            self.left >= outer.left
            and self.bottom >= outer.bottom
            and self.right <= outer.right
            and self.top <= outer.top
        )


def rectangles_overlap(rect_a: Rect, rect_b: Rect) -> bool:
    horizontal_overlap = rect_a.left < rect_b.right and rect_a.right > rect_b.left
    vertical_overlap = rect_a.bottom < rect_b.top and rect_a.top > rect_b.bottom
    return horizontal_overlap and vertical_overlap
