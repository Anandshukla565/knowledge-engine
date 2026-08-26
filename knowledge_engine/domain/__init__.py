"""Independent domain model boundary for the architecture mirror."""

from .findings import InputCompleteness, Scorecard, ValidationIssue, ValidationReport
from .floor_plan import FloorPlanSchema, ParkingSchema, PlotSchema, RoomSchema
from .rule import Rule, SourceRefs
from .suggestions import ExecutiveSummary, Suggestion, SuggestionReport

__all__ = [
    "ExecutiveSummary",
    "FloorPlanSchema",
    "InputCompleteness",
    "ParkingSchema",
    "PlotSchema",
    "Rule",
    "RoomSchema",
    "Scorecard",
    "SourceRefs",
    "Suggestion",
    "SuggestionReport",
    "ValidationIssue",
    "ValidationReport",
]
