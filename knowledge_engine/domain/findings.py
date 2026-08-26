from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

PRACTICAL_CHECKS_MESSAGE = "Practical checks are MVP heuristics, not legal/code compliance."
OFFICIAL_VASTU_MESSAGE = "No approved Vastu rules available. Candidate rules are not used for official scoring."
# Stage 2 single-source-of-truth vastu status.
# - "official" : approved rules with provenance; vastu_score is meaningful
# - "observations_only" : observations provided, but vastu_score stays None
VASTU_STATUS_VALUES = ("official", "observations_only")
VASTU_STATUS_OBSERVATIONS_ONLY = "observations_only"
VASTU_STATUS_OFFICIAL = "official"

EvidenceClassification = Literal[
    "CONFIRMED_ISSUE",
    "MISSING_INPUT_DATA",
    "POSSIBLE_ISSUE_REQUIRES_REVIEW",
    "INFORMATIONAL_OBSERVATION",
    "PROVISIONAL_VASTU_NOTE",
]

CONFIRMED_ISSUE_CODES = {
    "room_area_mismatch",
    "room_outside_plot",
    "room_overlap",
    "parking_outside_plot",
    "parking_room_overlap",
}
MISSING_INPUT_CODES = {
    "input_not_found",
    "invalid_json",
    "schema_validation_error",
    "missing_room_door",
    "missing_service_ventilation",
    "missing_circulation",
    "input_review_incomplete",
    "input_human_review_required",
    "input_confirmation_required",
}
POSSIBLE_REVIEW_CODES = {
    "small_bedroom_area",
    "small_master_bedroom_area",
    "small_kitchen_area",
    "small_toilet_area",
    "small_bathroom_area",
    "small_pooja_area",
    "small_parking_size",
    "rotated_plot_axis_alignment_warning",
}
PROVISIONAL_VASTU_CODES = {"brahmasthan_obstruction_risk"}


def evidence_classification_for_code(code: str) -> EvidenceClassification:
    if code in PROVISIONAL_VASTU_CODES:
        return "PROVISIONAL_VASTU_NOTE"
    if code in MISSING_INPUT_CODES:
        return "MISSING_INPUT_DATA"
    if code in CONFIRMED_ISSUE_CODES or code.startswith("missing_required_"):
        return "CONFIRMED_ISSUE"
    if code in POSSIBLE_REVIEW_CODES:
        return "POSSIBLE_ISSUE_REQUIRES_REVIEW"
    return "INFORMATIONAL_OBSERVATION"


class ValidationIssue(BaseModel):
    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    location: str | None = None
    evidence_classification: EvidenceClassification = "INFORMATIONAL_OBSERVATION"

    @model_validator(mode="before")
    @classmethod
    def derive_evidence_classification(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get("evidence_classification"):
            data = dict(data)
            data["evidence_classification"] = evidence_classification_for_code(str(data.get("code") or ""))
        return data


class ProvisionalCandidateNote(BaseModel):
    note_id: str
    severity: Literal["info", "warning", "review"]
    room_id: str | None = None
    room_type: str | None = None
    zone: str | None = None
    message: str
    candidate_basis: Literal["candidate_only_provisional"] = "candidate_only_provisional"
    official_scoring_used: bool = False
    source_verification_status: Literal["provisional"] = "provisional"
    evidence_classification: Literal["PROVISIONAL_VASTU_NOTE"] = "PROVISIONAL_VASTU_NOTE"


class InputCompleteness(BaseModel):
    """Additive record of whether supplied plan geometry may be validated."""

    status: Literal["not_applicable", "complete", "incomplete"] = "not_applicable"
    source_kind: str = "manual_json"
    review_status: str = "not_applicable"
    geometry_validation_blocked: bool = False
    unresolved_items: list[str] = Field(default_factory=list)
    message: str = "Structured JSON input is eligible for validation."


class ValidationReport(BaseModel):
    project_name: str
    input_path: str
    output_path: str
    generated_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    schema_valid: bool
    geometry_valid: bool
    requirements_valid: bool
    is_valid: bool
    validation_status: Literal["pass", "fail"]
    room_count: int = 0
    parking_count: int = 0
    issues: list[ValidationIssue] = Field(default_factory=list)
    provisional_notes: list[str] = Field(default_factory=list)
    provisional_candidate_notes: list[ProvisionalCandidateNote] = Field(default_factory=list)
    vertical_stack_observations: list[dict[str, Any]] = Field(default_factory=list)
    official_vastu_scoring_used: bool = False
    candidate_notes_used_for_scoring: bool = False
    practical_checks_message: str = PRACTICAL_CHECKS_MESSAGE
    plot_summary: dict[str, Any] = Field(default_factory=dict)
    input_completeness: InputCompleteness = Field(default_factory=InputCompleteness)


class Scorecard(BaseModel):
    scoring_mode: Literal["phase1_geometry_practical_only"] = "phase1_geometry_practical_only"
    geometry_score: float = 0.0
    completeness_score: float = 0.0
    practical_score: float = 0.0
    vastu_score: float | None = None
    vastu_excluded_from_overall: bool = True
    overall_score: float = 0.0
    official_vastu_message: str = OFFICIAL_VASTU_MESSAGE
    official_vastu_scoring_message: str = OFFICIAL_VASTU_MESSAGE
    official_vastu_scoring_used: bool = False
    candidate_notes_used_for_scoring: bool = False
    practical_checks_message: str = PRACTICAL_CHECKS_MESSAGE
    provisional_notes: list[str] = Field(default_factory=list)

