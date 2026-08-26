from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DraftPlanRequest(BaseModel):
    """Explicit requirements accepted by the draft planner."""

    plot_width_ft: float = Field(gt=0)
    plot_depth_ft: float = Field(gt=0)
    facing: str
    road_side: str | None = None
    bhk: int = Field(default=2, ge=1, le=12)
    bathrooms: int = Field(default=1, ge=0, le=12)
    kitchens: int = Field(default=1, ge=1, le=4)
    floors: int = Field(default=1, ge=1, le=5)
    attached_bathrooms: int = Field(default=0, ge=0, le=12)
    requires_parking: bool = False
    requires_pooja: bool = False
    space_type: Literal["residential", "commercial", "mixed_use"] = "residential"
    project_name: str = "Knowledge Engine Draft Plan"
    source_prompt: str | None = None

    def to_project_brief(self) -> dict:
        return {
            "project_name": self.project_name,
            "source_prompt": self.source_prompt,
            "plot": {
                "width": self.plot_width_ft,
                "depth": self.plot_depth_ft,
                "width_ft": self.plot_width_ft,
                "depth_ft": self.plot_depth_ft,
                "facing": self.facing,
                "road_side": self.road_side or self.facing,
            },
            "requirements": {
                "bedrooms": self.bhk,
                "bathrooms": self.bathrooms,
                "kitchens": self.kitchens,
                "floors": self.floors,
                "attached_bathrooms": self.attached_bathrooms or self.bhk,
                "requires_parking": self.requires_parking,
                "requires_pooja": self.requires_pooja,
            },
            "space_type": self.space_type,
            "rooms": [],
            "mock_llm": True,
            "status": "draft",
        }

    @property
    def road_side_or_facing(self) -> str:
        return self.road_side or self.facing
