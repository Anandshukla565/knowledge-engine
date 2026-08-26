from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SuggestionCategory = Literal[
    "geometry",
    "requirements",
    "practical",
    "vertical_stacking",
    "provisional_vastu",
    "report_quality",
]
SuggestionSeverity = Literal["info", "warning", "review", "critical"]
EvidenceClassification = Literal[
    "CONFIRMED_ISSUE",
    "MISSING_INPUT_DATA",
    "POSSIBLE_ISSUE_REQUIRES_REVIEW",
    "INFORMATIONAL_OBSERVATION",
    "PROVISIONAL_VASTU_NOTE",
]
PriorityBucket = Literal[
    "urgent_review",
    "important_review",
    "practical_improvement",
    "provisional_vastu_note",
    "report_quality_note",
]

PHASE2_DISCLAIMERS = [
    "Official Vastu scoring is disabled.",
    "Candidate/provisional notes are not used for scoring.",
    "Suggestions are deterministic review aids, not automatic design fixes.",
    "No legal/NBC/RERA/municipal compliance is included.",
    "No structural/load-path/plumbing guarantee is included.",
]


class Suggestion(BaseModel):
    suggestion_id: str
    category: SuggestionCategory
    severity: SuggestionSeverity
    evidence_classification: EvidenceClassification = "INFORMATIONAL_OBSERVATION"
    source_issue_code: str | None = None
    room_id: str | None = None
    target: str | None = None
    message: str
    recommended_action: str
    confidence: float = Field(ge=0.0, le=1.0)
    priority_rank: int = Field(default=0, ge=0)
    priority_score: float = Field(default=0.0, ge=0.0)
    priority_bucket: PriorityBucket = "practical_improvement"
    official_scoring_used: bool = False
    requires_human_review: bool = False
    auto_fix_available: bool = False


class ExecutiveSummary(BaseModel):
    total_suggestions: int = 0
    urgent_review_count: int = 0
    important_review_count: int = 0
    practical_improvement_count: int = 0
    provisional_vastu_note_count: int = 0
    top_3_suggestions: list[Suggestion] = Field(default_factory=list)
    key_message: str = ""
    official_scoring_used: bool = False
    auto_fix_available: bool = False


class SuggestionReport(BaseModel):
    report_version: str = "0.3.0"
    generated_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    input_validation_report: str
    input_scorecard: str
    input_phase1_plan: str | None = None
    scoring_mode: str | None = None
    official_vastu_scoring_used: bool = False
    candidate_notes_used_for_scoring: bool = False
    vastu_score: float | None = None
    disclaimers: list[str] = Field(default_factory=lambda: list(PHASE2_DISCLAIMERS))
    executive_summary: ExecutiveSummary = Field(default_factory=ExecutiveSummary)
    suggestions: list[Suggestion] = Field(default_factory=list)
    suggestion_count_by_category: dict[str, int] = Field(default_factory=dict)
    suggestion_count_by_priority_bucket: dict[str, int] = Field(default_factory=dict)
    suggestion_count_by_evidence_classification: dict[str, int] = Field(default_factory=dict)
