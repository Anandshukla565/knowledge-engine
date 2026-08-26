from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ACCEPTED_ROOM_TYPES = {
    "living",
    "kitchen",
    "bedroom",
    "master_bedroom",
    "staircase",
    "toilet",
    "bathroom",
    "pooja",
    "dining",
    "utility",
    "parking",
    "circulation",
    "other",
}


class MetadataSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    plan_id: str
    project_name: str
    project_id: str | None = None
    source_prompt: str | None = None
    units: str = "ft"
    level_count: int = Field(default=1, ge=1)
    schema_version: str = "0.1.0"


class PlotSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    width_ft: float = Field(gt=0)
    depth_ft: float = Field(gt=0)
    facing: str
    road_side: str
    north_angle_deg: float = 0.0
    setbacks: dict[str, float] | None = None
    multi_floor_context: bool | None = None
    small_plot_context: bool | None = None


class RequirementSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    bhk: int = Field(default=2, ge=0)
    required_bedrooms_count: int | None = Field(default=None, ge=0)
    requires_parking: bool = False
    requires_pooja: bool = False
    single_story_only: bool = True
    required_room_types: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def populate_required_bedrooms_count(self) -> "RequirementSchema":
        if self.required_bedrooms_count is None:
            self.required_bedrooms_count = self.bhk
        return self


class RoomSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    type: str
    name: str
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    area: float | None = Field(default=None, gt=0)
    level: int = Field(default=0, ge=0)
    doors: list[str] = Field(default_factory=list)
    windows: list[str] = Field(default_factory=list)
    vents: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    center_treatment: str | None = None
    is_open_area: bool | None = None
    is_heavy_storage: bool | None = None
    is_wet_service: bool | None = None
    is_structural_element: bool | None = None

    @field_validator("type")
    @classmethod
    def validate_room_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ACCEPTED_ROOM_TYPES:
            raise ValueError(f"Unsupported room type: {value}")
        return normalized

    @model_validator(mode="after")
    def populate_area(self) -> "RoomSchema":
        calculated_area = round(self.width * self.height, 2)
        if self.area is None:
            self.area = calculated_area
        return self


class ParkingSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    level: int = Field(default=0, ge=0)
    vehicle_type: str = "car"
    inside_plot: bool | None = None
    notes: list[str] = Field(default_factory=list)


class BrahmasthanTreatmentSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str | None = None
    open_to_sky: bool | None = None
    double_height: bool | None = None
    light_well: bool | None = None
    notes: list[str] = Field(default_factory=list)


class InputProvenanceSchema(BaseModel):
    """Optional review state for future drawing-derived Phase 1 JSON.

    The validator remains JSON-only. This metadata prevents a future PDF/DWG
    extraction draft from being treated as confirmed plan geometry.
    """

    model_config = ConfigDict(extra="ignore")

    source_kind: Literal["manual_json", "drawing_extraction_draft", "reviewed_drawing"] = "manual_json"
    review_status: Literal["not_applicable", "draft", "reviewed", "incomplete"] = "not_applicable"
    source_document_id: str | None = None
    source_document_checksum: str | None = None
    source_page_numbers: list[int] = Field(default_factory=list)
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    unresolved_items: list[str] = Field(default_factory=list)


class FloorPlanSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    metadata: MetadataSchema
    plot: PlotSchema
    requirements: RequirementSchema
    rooms: list[RoomSchema] = Field(default_factory=list)
    openings: list[dict[str, Any]] = Field(default_factory=list)
    parking: list[ParkingSchema] = Field(default_factory=list)
    services: dict[str, Any] = Field(default_factory=dict)
    brahmasthan_treatment: BrahmasthanTreatmentSchema | None = None
    input_provenance: InputProvenanceSchema | None = None
    notes: list[str] = Field(default_factory=list)


