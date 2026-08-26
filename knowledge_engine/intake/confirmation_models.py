from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

CONFIRMATION_STATUS = Literal["UNREVIEWED", "CONFIRMED", "CORRECTED", "UNKNOWN", "NOT_APPLICABLE"]
EVIDENCE_TYPE = Literal["EXPLICIT_INPUT", "DERIVED_DETERMINISTICALLY", "INFERRED_REQUIRES_CONFIRMATION", "MISSING_INPUT"]
FACT_CATEGORY = Literal["required_geometry", "required_practical", "required_specific", "optional"]
READINESS_RESULT = Literal["READY_FOR_PRELIMINARY_REVIEW", "NOT_READY_MISSING_REQUIRED_INPUT", "READY_WITH_LIMITATIONS"]
CONFIDENCE = Literal["high", "medium", "low"]

# ---------------------------------------------------------------------------
# Supporting models
# ---------------------------------------------------------------------------


class Provenance(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_path: str = Field(description="Source field or section in the input.")
    source_field: str = Field(description="Specific field or derivation method.")
    derivation_note: str | None = Field(default=None, description="Explanation for derived/inferred facts.")


class Fact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    fact_id: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    label: str
    value: Any = Field(default=None)
    evidence_type: EVIDENCE_TYPE
    category: FACT_CATEGORY
    provenance: Provenance


class MissingItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    fact_id: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    label: str
    category: FACT_CATEGORY
    blocking: bool = False
    required_for: list[str] = Field(default_factory=list)


class InferredItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    fact_id: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    label: str
    inferred_value: Any
    confidence: CONFIDENCE
    category: FACT_CATEGORY
    blocking: bool = False
    derivation_note: str | None = None


class ResponseItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    fact_id: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    status: CONFIRMATION_STATUS
    corrected_value: Any = Field(default=None)
    note: str | None = None
    original_value: Any = Field(default=None)
    response_timestamp: datetime | None = None

    @model_validator(mode="after")
    def validate_corrected_requires_value(self) -> "ResponseItem":
        if self.status == "CORRECTED" and self.corrected_value is None:
            raise ValueError("CORRECTED responses must include corrected_value.")
        if self.status == "CONFIRMED":
            self.corrected_value = None
        return self


class BlockingItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    item_id: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    label: str
    reason: str
    related_fact_ids: list[str] = Field(default_factory=list)


class CompletenessSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    required_fields_present: bool
    optional_fields_present: int = Field(ge=0)
    total_facts: int = Field(ge=0)
    missing_count: int = Field(ge=0)
    inferred_count: int = Field(ge=0)
    blocking_count: int = Field(ge=0)


class ValidationReadiness(BaseModel):
    model_config = ConfigDict(extra="ignore")

    result: READINESS_RESULT
    reason: str
    geometry_ready: bool = False
    practical_ready: bool = False
    limitations: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Top-level confirmation package
# ---------------------------------------------------------------------------


class InputConfirmation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: Literal["1.0.0"] = "1.0.0"
    source_input_path: str
    source_input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    generated_at: datetime | None = Field(default=None)
    product_checkpoint_used: str
    overall_status: CONFIRMATION_STATUS = "UNREVIEWED"
    completeness_summary: CompletenessSummary
    extracted_facts: list[Fact] = Field(default_factory=list)
    missing_information: list[MissingItem] = Field(default_factory=list)
    inferred_information: list[InferredItem] = Field(default_factory=list)
    architect_responses: list[ResponseItem] = Field(default_factory=list)
    blocking_items: list[BlockingItem] = Field(default_factory=list)
    non_blocking_items: list[BlockingItem] = Field(default_factory=list)
    validation_readiness: ValidationReadiness


# ---------------------------------------------------------------------------
# Response batch (for the confirm CLI)
# ---------------------------------------------------------------------------


class ResponseBatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    responses: list[ResponseItem]

    @model_validator(mode="after")
    def validate_unique_fact_ids(self) -> "ResponseBatch":
        seen: set[str] = set()
        duplicates: list[str] = []
        for response in self.responses:
            if response.fact_id in seen:
                duplicates.append(response.fact_id)
            seen.add(response.fact_id)
        if duplicates:
            raise ValueError(f"Duplicate fact_id responses: {duplicates}")
        return self
