from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from knowledge_engine.domain.findings import Scorecard, ValidationReport
from knowledge_engine.domain.floor_plan import FloorPlanSchema
from knowledge_engine.domain.suggestions import ExecutiveSummary, Suggestion, SuggestionReport
from knowledge_engine.infrastructure.file_storage import write_if_changed
from knowledge_engine.reports.annotation_mapper import build_plan_annotations
from knowledge_engine.reports.json_renderer import (
    render_architect_review_markdown,
    render_suggestion_markdown,
)

GEOMETRY_CODES = {"room_overlap", "room_outside_plot", "invalid_dimensions"}
REQUIREMENT_CODES = {"missing_pooja", "missing_parking", "missing_bedroom"}
PRACTICAL_ACTIONS = {
    "small_kitchen_area": "Review kitchen area and increase it or mark the compact-kitchen exception explicitly.",
    "small_bedroom_area": "Review bedroom area and increase it or mark the compact-bedroom exception explicitly.",
    "small_master_bedroom_area": "Review master bedroom area and increase it or mark the compact-master-bedroom exception explicitly.",
    "small_toilet_area": "Review toilet area and increase it or mark the compact-toilet exception explicitly.",
    "small_bathroom_area": "Review bathroom area and increase it or mark the compact-bathroom exception explicitly.",
    "small_parking_size": "Review parking bay dimensions and increase them or mark the compact-parking exception explicitly.",
    "missing_room_door": "Door information was not supplied in the input. Confirm the entrance in the design documentation.",
    "missing_service_ventilation": "Window or ventilation information was not supplied in the input. Confirm window, duct, shaft, or vent provision in the design documentation.",
    "missing_circulation": "Circulation-space information was not supplied in the input. Confirm whether a hall, lobby, stair connector, or other circulation space exists in the design documentation.",
}
PRACTICAL_MESSAGES = {
    "missing_room_door": "Door information was not supplied in the input for this room. This does not prove the physical door is absent; confirm the design documentation.",
    "missing_service_ventilation": "Window or ventilation information was not supplied in the input for this service room. Confirm the design documentation.",
}
PRACTICAL_CODES = set(PRACTICAL_ACTIONS)
EVIDENCE_CLASSIFICATIONS = {
    "CONFIRMED_ISSUE",
    "MISSING_INPUT_DATA",
    "POSSIBLE_ISSUE_REQUIRES_REVIEW",
    "INFORMATIONAL_OBSERVATION",
    "PROVISIONAL_VASTU_NOTE",
}
MISSING_INPUT_PRACTICAL_CODES = {"missing_room_door", "missing_service_ventilation", "missing_circulation"}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _stable_payload_for_existing_generated_at(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if not path.exists() or "generated_at" not in payload:
        return payload
    try:
        existing = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return payload
    existing_without_time = dict(existing)
    payload_without_time = dict(payload)
    existing_time = existing_without_time.pop("generated_at", None)
    payload_without_time.pop("generated_at", None)
    if existing_time and existing_without_time == payload_without_time:
        stable = dict(payload)
        stable["generated_at"] = existing_time
        return stable
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> bool:
    stable_payload = _stable_payload_for_existing_generated_at(path, payload)
    return write_if_changed(path, json.dumps(stable_payload, indent=2, ensure_ascii=False) + "\n")


def _room_id_from_location(location: str | None) -> str | None:
    if not location:
        return None
    match = re.search(r"rooms\.([^.]+)", location)
    if match:
        return match.group(1)
    return None


def _slug(value: str | None) -> str:
    text = (value or "unknown").lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or "unknown"


def _base_suggestion(
    *,
    suggestion_id: str,
    category: str,
    severity: str,
    evidence_classification: str,
    source_issue_code: str | None,
    room_id: str | None,
    target: str | None,
    message: str,
    recommended_action: str,
    confidence: float,
    requires_human_review: bool,
) -> Suggestion:
    return Suggestion(
        suggestion_id=suggestion_id,
        category=category,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        evidence_classification=evidence_classification,  # type: ignore[arg-type]
        source_issue_code=source_issue_code,
        room_id=room_id,
        target=target,
        message=message,
        recommended_action=recommended_action,
        confidence=confidence,
        official_scoring_used=False,
        requires_human_review=requires_human_review,
        auto_fix_available=False,
    )


def _issue_evidence(issue: dict[str, Any], fallback: str) -> str:
    value = str(issue.get("evidence_classification") or "")
    return value if value in EVIDENCE_CLASSIFICATIONS else fallback


def _map_issue(issue: dict[str, Any], index: int) -> Suggestion | None:
    code = str(issue.get("code") or "")
    location = issue.get("location")
    room_id = _room_id_from_location(location)
    original_message = str(issue.get("message") or code)
    message = PRACTICAL_MESSAGES.get(code, original_message)

    if code in GEOMETRY_CODES:
        return _base_suggestion(
            suggestion_id=f"suggestion_geometry_{index:03d}_{_slug(code)}",
            category="geometry",
            severity="critical",
            evidence_classification=_issue_evidence(issue, "CONFIRMED_ISSUE"),
            source_issue_code=code,
            room_id=room_id,
            target=location,
            message=message,
            recommended_action="Fix the geometry issue in the source plan before evaluating planning quality.",
            confidence=0.95,
            requires_human_review=True,
        )

    if code in REQUIREMENT_CODES:
        return _base_suggestion(
            suggestion_id=f"suggestion_requirements_{index:03d}_{_slug(code)}",
            category="requirements",
            severity="review",
            evidence_classification=_issue_evidence(issue, "CONFIRMED_ISSUE"),
            source_issue_code=code,
            room_id=room_id,
            target=location,
            message=message,
            recommended_action="Add the missing required space or explicitly update the project requirements.",
            confidence=0.9,
            requires_human_review=True,
        )

    if code in PRACTICAL_CODES:
        return _base_suggestion(
            suggestion_id=f"suggestion_practical_{index:03d}_{_slug(code)}",
            category="practical",
            severity="warning",
            evidence_classification=_issue_evidence(
                issue,
                "MISSING_INPUT_DATA" if code in MISSING_INPUT_PRACTICAL_CODES else "POSSIBLE_ISSUE_REQUIRES_REVIEW",
            ),
            source_issue_code=code,
            room_id=room_id,
            target=location,
            message=message,
            recommended_action=PRACTICAL_ACTIONS[code],
            confidence=0.85,
            requires_human_review=False,
        )

    if code == "brahmasthan_obstruction_risk":
        return _base_suggestion(
            suggestion_id=f"suggestion_provisional_vastu_{index:03d}_{_slug(code)}",
            category="provisional_vastu",
            severity="review",
            evidence_classification=_issue_evidence(issue, "PROVISIONAL_VASTU_NOTE"),
            source_issue_code=code,
            room_id=room_id,
            target=location,
            message=message,
            recommended_action="Review center-zone obstruction risk as provisional Vastu metadata only; do not treat it as an official violation or scoring penalty.",
            confidence=0.75,
            requires_human_review=True,
        )

    if code == "multi_floor_level_aware_overlap_check":
        return _base_suggestion(
            suggestion_id=f"suggestion_report_quality_{index:03d}_{_slug(code)}",
            category="report_quality",
            severity="info",
            evidence_classification=_issue_evidence(issue, "INFORMATIONAL_OBSERVATION"),
            source_issue_code=code,
            room_id=room_id,
            target=location,
            message="Report metadata note: multi-floor overlap checks are level-aware, while vertical stacking is reported separately for review.",
            recommended_action="Operator note: explain this separation during the demo so it is not mistaken for a client design defect.",
            confidence=0.9,
            requires_human_review=False,
        )

    return None


def _vertical_action(observation: dict[str, Any]) -> str:
    lower = str(observation.get("lower_room_type") or "").lower()
    upper = str(observation.get("upper_room_type") or "").lower()
    message = str(observation.get("message") or "").lower()
    if "bathroom" in {lower, upper} and "kitchen" in {lower, upper}:
        return "This bathroom-over-kitchen stacking arrangement requires architect and consultant coordination review for plumbing, drainage, waterproofing, ceiling, and service-routing implications."
    if "bathroom" in {lower, upper} and ("bedroom" in {lower, upper} or "master_bedroom" in {lower, upper} or "pooja" in {lower, upper}):
        return "This bathroom and bedroom/pooja stacking relationship requires architect and consultant coordination review for wet-service, acoustic, privacy, and routing implications."
    if "staircase" in {lower, upper} and ("bedroom" in {lower, upper} or "master_bedroom" in {lower, upper}):
        return "This staircase and bedroom stacking relationship requires architect and consultant coordination review for structure, circulation, and planning implications."
    if "brahmasthan" in message:
        return "Review stacked footprint through the provisional Brahmasthan center zone; this is review metadata only."
    return "Review this vertical stack with an architect/engineer; this is metadata only and not an automatic failure."


def _map_vertical_observation(observation: dict[str, Any], index: int) -> Suggestion:
    lower_id = observation.get("lower_room_id")
    upper_id = observation.get("upper_room_id")
    target = f"lower:{lower_id}/upper:{upper_id}"
    return _base_suggestion(
        suggestion_id=f"suggestion_vertical_stacking_{index:03d}_{_slug(str(lower_id))}_{_slug(str(upper_id))}",
        category="vertical_stacking",
        severity="review",
        evidence_classification=(
            str(observation.get("evidence_classification"))
            if str(observation.get("evidence_classification")) in EVIDENCE_CLASSIFICATIONS
            else "POSSIBLE_ISSUE_REQUIRES_REVIEW"
        ),
        source_issue_code="vertical_stack_observation",
        room_id=str(upper_id or lower_id or "") or None,
        target=target,
        message=str(observation.get("message") or "Vertical stacking review observation."),
        recommended_action=_vertical_action(observation),
        confidence=0.8,
        requires_human_review=True,
    )


def _map_candidate_note(note: dict[str, Any], index: int) -> Suggestion:
    note_id = str(note.get("note_id") or f"candidate_note_{index:03d}")
    raw_severity = str(note.get("severity") or "info")
    severity = raw_severity if raw_severity in {"info", "warning", "review"} else "info"
    message = str(note.get("message") or "Provisional candidate note.")
    recommended_action = "Treat as a provisional review note only. Do not use it for official scoring or automatic design changes."
    if "kitchen" in note_id.lower():
        recommended_action = "Review kitchen placement against the provisional Vastu preference. This is not official scoring and should be confirmed by a qualified reviewer if used."
    return _base_suggestion(
        suggestion_id=f"suggestion_provisional_vastu_{index:03d}_{_slug(note_id)}",
        category="provisional_vastu",
        severity=severity,
        evidence_classification=(
            str(note.get("evidence_classification"))
            if str(note.get("evidence_classification")) in EVIDENCE_CLASSIFICATIONS
            else "PROVISIONAL_VASTU_NOTE"
        ),
        source_issue_code=note_id,
        room_id=note.get("room_id"),
        target=note.get("zone") or note.get("room_type"),
        message=f"{message} This is not an approved production rule.",
        recommended_action=recommended_action,
        confidence=0.7,
        requires_human_review=severity == "review",
    )


def _priority_for(suggestion: Suggestion) -> tuple[float, str]:
    code = suggestion.source_issue_code or ""
    text = f"{suggestion.message} {suggestion.recommended_action}".lower()

    if suggestion.category == "geometry" and suggestion.severity == "critical":
        return 100.0, "urgent_review"
    if suggestion.category == "requirements":
        return 90.0, "urgent_review"
    if suggestion.category == "vertical_stacking":
        if "bathroom-over-kitchen" in text or "bathroom-over-bedroom" in text or "pooja" in text:
            return 88.0, "urgent_review"
        if "staircase-over-bedroom" in text:
            return 82.0, "important_review"
        if "brahmasthan" in text:
            return 78.0, "important_review"
        return 74.0, "important_review"
    if code == "brahmasthan_obstruction_risk":
        return 80.0, "important_review"
    if code == "missing_service_ventilation":
        return 70.0, "practical_improvement"
    if code == "missing_circulation":
        return 66.0, "practical_improvement"
    if code in {"small_kitchen_area", "small_bedroom_area", "small_master_bedroom_area", "small_toilet_area", "small_bathroom_area", "small_parking_size"}:
        return 62.0, "practical_improvement"
    if code == "missing_room_door":
        return 55.0, "practical_improvement"
    if suggestion.category == "provisional_vastu":
        return 40.0, "provisional_vastu_note"
    if suggestion.category == "report_quality":
        return 10.0, "report_quality_note"
    return 30.0, "practical_improvement"


def _apply_priorities(suggestions: list[Suggestion]) -> list[Suggestion]:
    for suggestion in suggestions:
        score, bucket = _priority_for(suggestion)
        suggestion.priority_score = score
        suggestion.priority_bucket = bucket  # type: ignore[assignment]

    sorted_suggestions = sorted(
        suggestions,
        key=lambda item: (-item.priority_score, item.category, item.suggestion_id),
    )
    for rank, suggestion in enumerate(sorted_suggestions, start=1):
        suggestion.priority_rank = rank
    return sorted_suggestions


def _build_executive_summary(suggestions: list[Suggestion]) -> ExecutiveSummary:
    bucket_counts = Counter(suggestion.priority_bucket for suggestion in suggestions)
    top_3 = suggestions[:3]
    if suggestions:
        key_message = (
            "Review the highest-priority planning and stacking items first. "
            "Suggestions are deterministic review aids only; official Vastu scoring and auto-fixes remain disabled."
        )
    else:
        key_message = "No Phase 2 suggestions were generated. Official Vastu scoring and auto-fixes remain disabled."
    return ExecutiveSummary(
        total_suggestions=len(suggestions),
        urgent_review_count=bucket_counts.get("urgent_review", 0),
        important_review_count=bucket_counts.get("important_review", 0),
        practical_improvement_count=bucket_counts.get("practical_improvement", 0),
        provisional_vastu_note_count=bucket_counts.get("provisional_vastu_note", 0),
        top_3_suggestions=top_3,
        key_message=key_message,
        official_scoring_used=False,
        auto_fix_available=False,
    )


def build_suggestion_report(
    validation_report_path: Path,
    scorecard_path: Path,
    phase1_input_path: Path | None = None,
) -> SuggestionReport:
    validation_report = _load_json(validation_report_path)
    scorecard = _load_json(scorecard_path)

    suggestions: list[Suggestion] = []
    for index, issue in enumerate(validation_report.get("issues") or [], start=1):
        if isinstance(issue, dict):
            suggestion = _map_issue(issue, index)
            if suggestion is not None:
                suggestions.append(suggestion)

    for index, observation in enumerate(validation_report.get("vertical_stack_observations") or [], start=1):
        if isinstance(observation, dict):
            suggestions.append(_map_vertical_observation(observation, index))

    for index, note in enumerate(validation_report.get("provisional_candidate_notes") or [], start=1):
        if isinstance(note, dict):
            suggestions.append(_map_candidate_note(note, index))

    suggestions = _apply_priorities(suggestions)
    category_counts = Counter(suggestion.category for suggestion in suggestions)
    bucket_counts = Counter(suggestion.priority_bucket for suggestion in suggestions)
    evidence_counts = Counter(suggestion.evidence_classification for suggestion in suggestions)
    return SuggestionReport(
        input_validation_report=str(validation_report_path),
        input_scorecard=str(scorecard_path),
        input_phase1_plan=str(phase1_input_path) if phase1_input_path else None,
        scoring_mode=scorecard.get("scoring_mode"),
        official_vastu_scoring_used=False,
        candidate_notes_used_for_scoring=False,
        vastu_score=None,
        executive_summary=_build_executive_summary(suggestions),
        suggestions=suggestions,
        suggestion_count_by_category=dict(sorted(category_counts.items())),
        suggestion_count_by_priority_bucket=dict(sorted(bucket_counts.items())),
        suggestion_count_by_evidence_classification=dict(sorted(evidence_counts.items())),
    )


def write_suggestion_outputs(report: SuggestionReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "suggestion_report.json"
    markdown_path = output_dir / "suggestion_report.md"
    _write_json(json_path, report.model_dump(mode="json"))
    write_if_changed(markdown_path, render_suggestion_markdown(report))
    return json_path, markdown_path


def write_architect_review_output(
    report: SuggestionReport,
    *,
    phase1_input_path: Path,
    validation_report_path: Path,
    scorecard_path: Path,
    output_path: Path,
) -> Path:
    plan = FloorPlanSchema.model_validate(_load_json(phase1_input_path))
    validation_report = ValidationReport.model_validate(_load_json(validation_report_path))
    scorecard = Scorecard.model_validate(_load_json(scorecard_path))
    annotations = build_plan_annotations(plan, validation_report)
    markdown = render_architect_review_markdown(
        plan,
        validation_report,
        scorecard,
        report,
        annotations,
    )
    write_if_changed(output_path, markdown)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 2 read-only suggestion engine")
    parser.add_argument("--validation-report", required=True, help="Phase 1 validation_report.json path")
    parser.add_argument("--scorecard", required=True, help="Phase 1 scorecard.json path")
    parser.add_argument("--phase1-input", help="Optional Phase 1 input JSON path")
    parser.add_argument("--out", required=True, help="Output folder for suggestion_report files")
    args = parser.parse_args()

    validation_report_path = Path(args.validation_report)
    scorecard_path = Path(args.scorecard)
    phase1_input_path = Path(args.phase1_input) if args.phase1_input else None
    output_dir = Path(args.out)

    report = build_suggestion_report(validation_report_path, scorecard_path, phase1_input_path)
    json_path, markdown_path = write_suggestion_outputs(report, output_dir)
    architect_report_path = None
    if phase1_input_path is not None:
        architect_report_path = write_architect_review_output(
            report,
            phase1_input_path=phase1_input_path,
            validation_report_path=validation_report_path,
            scorecard_path=scorecard_path,
            output_path=output_dir / "architect_review_report.md",
        )

    print(f"suggestion_report_json = {json_path}")
    print(f"suggestion_report_md = {markdown_path}")
    if architect_report_path is not None:
        print(f"architect_review_report = {architect_report_path}")
    print(f"suggestion_count = {len(report.suggestions)}")
    print(f"suggestion_count_by_category = {json.dumps(report.suggestion_count_by_category, sort_keys=True)}")
    print(f"suggestion_count_by_priority_bucket = {json.dumps(report.suggestion_count_by_priority_bucket, sort_keys=True)}")
    print(f"top_3_suggestions = {json.dumps([item.suggestion_id for item in report.executive_summary.top_3_suggestions])}")
    print("official_scoring_used = false")
    print("auto_fix_available = false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
