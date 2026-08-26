"""End-to-end provisional planner-to-validation/report workflow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from knowledge_engine.infrastructure.file_storage import write_if_changed
from knowledge_engine.validation.pipeline import run_validation_pipeline

from .draft_generator import DraftPlanRequest, generate_draft_project_state
from .usability import assess_architect_usability
from .validation_adapter import canonical_to_floor_plan_data
from knowledge_engine.domain.floor_plan import FloorPlanSchema


def run_generated_draft(
    request: DraftPlanRequest,
    output_dir: str | Path,
    *,
    seed: int | None = 0,
) -> dict[str, Any]:
    """Generate, validate, and render one provisional draft bundle.

    Blocked generation stops before normal validation or report rendering.
    """

    output_path = Path(output_dir)
    generated = generate_draft_project_state(request, seed=seed)
    if generated.get("generation_status") == "blocked":
        return {
            "generation_status": "blocked",
            "validation_status": "skipped",
            "blocked_reason": generated.get("blocked_reason"),
            "output_dir": output_path,
            "validation_dir": None,
        }

    output_path.mkdir(parents=True, exist_ok=True)
    draft_path = output_path / "planner_draft.json"
    input_path = output_path / "phase1_input.json"
    validation_dir = output_path / "validation"
    plan_data = canonical_to_floor_plan_data(generated, request)
    write_if_changed(draft_path, json.dumps(generated, indent=2, ensure_ascii=False) + "\n")
    write_if_changed(input_path, json.dumps(plan_data, indent=2, ensure_ascii=False) + "\n")
    validation = run_validation_pipeline(input_path, validation_dir, write_outputs=True)
    report = validation["report"]
    scorecard = validation["scorecard"]
    assessment = assess_architect_usability(FloorPlanSchema.model_validate(plan_data), report)
    assessment_path = output_path / "planner_assessment.json"
    write_if_changed(assessment_path, json.dumps(assessment.to_dict(), indent=2, ensure_ascii=False) + "\n")
    return {
        "generation_status": "draft" if assessment.architect_usable else "blocked",
        "validation_status": report.validation_status,
        "blocked_reason": "; ".join(assessment.blockers) if assessment.blockers else None,
        "output_dir": output_path,
        "validation_dir": validation_dir,
        "planner_draft_path": draft_path,
        "phase1_input_path": input_path,
        "validation_report_path": validation["validation_report_path"],
        "scorecard_path": validation["scorecard_path"],
        "report_md_path": validation["markdown_path"],
        "plan_svg_path": validation["svg_path"],
        "planner_assessment_path": assessment_path,
        "architect_usability": assessment.to_dict(),
        "template_id": generated.get("template_id"),
        "report": report,
        "scorecard": scorecard,
    }
