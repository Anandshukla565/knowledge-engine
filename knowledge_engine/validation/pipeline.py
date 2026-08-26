from __future__ import annotations

import argparse
import json
from pathlib import Path

from knowledge_engine.domain.findings import InputCompleteness, Scorecard, ValidationIssue, ValidationReport
from knowledge_engine.reports.annotation_mapper import build_plan_annotations
from knowledge_engine.reports.architect_report import render_markdown_report
from knowledge_engine.reports.svg_renderer import render_plan_svg
from knowledge_engine.validation.candidate_notes import extract_provisional_candidate_notes
from knowledge_engine.validation.geometry import validate_geometry
from knowledge_engine.validation.input_completeness import validate_input_completeness
from knowledge_engine.validation.practical import validate_practical
from knowledge_engine.validation.requirements import validate_requirements
from knowledge_engine.validation.scorecard import OFFICIAL_VASTU_DISABLED_MESSAGE, build_scorecard
from knowledge_engine.intake.json_adapter import load_floor_plan, validate_floor_plan_data
from knowledge_engine.infrastructure.file_storage import write_if_changed


def _stable_payload_for_existing_generated_at(path: Path, payload: dict) -> dict:
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


def _write_json(path: Path, payload: dict) -> bool:
    stable_payload = _stable_payload_for_existing_generated_at(path, payload)
    return write_if_changed(path, json.dumps(stable_payload, indent=2, ensure_ascii=False) + "\n")


def _has_errors(issues: list[ValidationIssue]) -> bool:
    return any(issue.severity == "error" for issue in issues)


def build_validation_report(input_path: Path, output_path: Path) -> tuple[ValidationReport, Scorecard, str, str]:
    raw_data, plan, schema_issues = load_floor_plan(input_path)
    geometry_issues: list[ValidationIssue] = []
    requirement_issues: list[ValidationIssue] = []
    practical_issues: list[ValidationIssue] = []
    practical_data: dict[str, object] = {}
    input_completeness = InputCompleteness()
    input_issues: list[ValidationIssue] = []
    provisional_candidate_notes = []
    project_name = input_path.stem
    plot_summary: dict[str, object] = {}
    vertical_stack_observations: list[dict[str, object]] = []
    svg_content = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"400\" height=\"120\"><text x=\"20\" y=\"60\">Schema invalid. No preview available.</text></svg>\n"
    if plan is not None:
        project_name = plan.metadata.project_name
        input_completeness, input_issues = validate_input_completeness(plan)
        if not input_issues:
            geometry_issues, geometry_data = validate_geometry(plan)
            requirement_issues = validate_requirements(plan)
            practical_issues, practical_data = validate_practical(plan)
            provisional_candidate_notes = extract_provisional_candidate_notes(plan, geometry_data)
            plot_summary = {
                "width_ft": plan.plot.width_ft,
                "depth_ft": plan.plot.depth_ft,
                "facing": plan.plot.facing,
                "road_side": plan.plot.road_side,
                "north_angle_deg": plan.plot.north_angle_deg,
                "room_count": len(plan.rooms),
                "parking_count": len(plan.parking),
                "room_zones": geometry_data["room_zones"],
                "zone_tagging_mode": geometry_data["zone_tagging_mode"],
                "rotation_warning_applies": geometry_data["rotation_warning_applies"],
                "brahmasthan_bounds": geometry_data["brahmasthan_bounds"],
                "brahmasthan_observation": geometry_data["brahmasthan_observation"],
                "vertical_stack_observations": geometry_data["vertical_stack_observations"],
                "practical_issue_count": practical_data.get("practical_issue_count", 0),
                "practical_issue_codes": practical_data.get("practical_issue_codes", []),
            }
            vertical_stack_observations = list(geometry_data["vertical_stack_observations"])
    issues = schema_issues + input_issues + geometry_issues + requirement_issues + practical_issues
    schema_valid = not _has_errors(schema_issues)
    geometry_blocked = bool(input_completeness and input_completeness.geometry_validation_blocked)
    geometry_valid = schema_valid and not geometry_blocked and not _has_errors(geometry_issues)
    requirements_valid = schema_valid and not geometry_blocked and not _has_errors(requirement_issues)
    report = ValidationReport(
        project_name=project_name,
        input_path=str(input_path),
        output_path=str(output_path),
        schema_valid=schema_valid,
        geometry_valid=geometry_valid,
        requirements_valid=requirements_valid,
        is_valid=schema_valid and geometry_valid and requirements_valid,
        validation_status="pass" if (schema_valid and geometry_valid and requirements_valid) else "fail",
        room_count=len(plan.rooms) if plan is not None else len((raw_data or {}).get("rooms", [])),
        parking_count=len(plan.parking) if plan is not None else len((raw_data or {}).get("parking", [])),
        issues=issues,
        provisional_notes=[
            OFFICIAL_VASTU_DISABLED_MESSAGE,
            "Candidate rules are not used for official scoring in Phase 1 foundation mode.",
            "Brahmasthan observations are provisional center-zone checks, not official Vastu scoring.",
        ],
        provisional_candidate_notes=provisional_candidate_notes,
        vertical_stack_observations=vertical_stack_observations,
        official_vastu_scoring_used=False,
        candidate_notes_used_for_scoring=False,
        practical_checks_message=str(practical_data.get("practical_checks_message", "Practical checks are MVP heuristics, not legal/code compliance.")),
        plot_summary=plot_summary,
        input_completeness=input_completeness,
    )
    scorecard = build_scorecard(report)
    display_plan = plan if plan is not None and not geometry_blocked else None
    annotations = build_plan_annotations(display_plan, report) if display_plan is not None else None
    markdown = render_markdown_report(display_plan, report, scorecard, annotations=annotations)
    if display_plan is not None:
        svg_content = render_plan_svg(display_plan, annotations=annotations)
    return report, scorecard, markdown, svg_content


def run_validation_pipeline(input_path: str | Path, output_dir: str | Path, write_outputs: bool = True) -> dict[str, object]:
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report, scorecard, markdown, svg_content = build_validation_report(input_path, output_dir)

    validation_report_path = output_dir / "validation_report.json"
    scorecard_path = output_dir / "scorecard.json"
    markdown_path = output_dir / "report.md"
    svg_path = output_dir / "plan.svg"

    if write_outputs:
        _write_json(validation_report_path, report.model_dump(mode="json"))
        _write_json(scorecard_path, scorecard.model_dump(mode="json"))
        write_if_changed(markdown_path, markdown)
        svg_path.write_text(svg_content, encoding="utf-8")

    return {
        "report": report,
        "scorecard": scorecard,
        "markdown": markdown,
        "svg_content": svg_content,
        "output_dir": output_dir,
        "validation_report_path": validation_report_path,
        "scorecard_path": scorecard_path,
        "markdown_path": markdown_path,
        "svg_path": svg_path,
    }


def _format_cli_output(result: dict[str, object]) -> str:
    report = result["report"]
    scorecard = result["scorecard"]
    assert isinstance(report, ValidationReport)
    assert isinstance(scorecard, Scorecard)
    lines = [
        f"validation_status = {report.validation_status}",
        f"validation_report = {result['validation_report_path']}",
        f"scorecard = {result['scorecard_path']}",
        f"report_md = {result['markdown_path']}",
        f"plan_svg = {result['svg_path']}",
        f"vastu_score = {scorecard.vastu_score}",
        scorecard.official_vastu_scoring_message,
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 1 floor-plan validator foundation CLI"
    )
    parser.add_argument("--input", required=True, help="Input floor-plan JSON path")
    parser.add_argument("--out", required=True, help="Output directory")
    args = parser.parse_args()

    result = run_validation_pipeline(args.input, args.out)
    print(_format_cli_output(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
