"""Read-only smoke checks for the extracted planner boundary."""

from pathlib import Path
from tempfile import TemporaryDirectory

from knowledge_engine.planning import (
    DraftPlanRequest,
    generate_draft_project_state,
    run_generated_draft,
    validate_generated_draft,
)


def main() -> int:
    draft = generate_draft_project_state(
        DraftPlanRequest(
            plot_width_ft=30,
            plot_depth_ft=40,
            facing="east",
            road_side="east",
            bhk=3,
            bathrooms=1,
            floors=1,
        ),
        seed=7,
    )
    complete_request = DraftPlanRequest(
        plot_width_ft=30,
        plot_depth_ft=40,
        facing="east",
        road_side="east",
        bhk=3,
        bathrooms=3,
        floors=1,
        requires_parking=True,
        requires_pooja=True,
    )
    complete = generate_draft_project_state(complete_request, seed=7)
    if draft.get("generation_status") != "draft":
        raise AssertionError("baseline planner request did not produce a draft")
    impossible = generate_draft_project_state(
        DraftPlanRequest(
            plot_width_ft=30,
            plot_depth_ft=40,
            facing="east",
            road_side="east",
            bhk=12,
            bathrooms=12,
            floors=1,
            requires_parking=True,
            requires_pooja=True,
        ),
        seed=7,
    )
    if complete.get("generation_status") != "draft":
        raise AssertionError("supported special-space request did not produce a draft")
    validated = validate_generated_draft(complete, complete_request)
    if validated.get("validation_status") != "pass":
        raise AssertionError("supported planner draft did not pass validation")
    if validated["scorecard"].vastu_score is not None:
        raise AssertionError("official Vastu score was unexpectedly populated")
    with TemporaryDirectory(prefix="knowledge_engine_bundle_smoke_") as directory:
        bundle = run_generated_draft(complete_request, Path(directory) / "bundle", seed=7)
        if bundle.get("validation_status") != "pass":
            raise AssertionError("report bundle workflow did not pass validation")
        if not bundle["report_md_path"].exists() or not bundle["plan_svg_path"].exists():
            raise AssertionError("report bundle did not produce Markdown and SVG")
    if impossible.get("generation_status") != "blocked":
        raise AssertionError("unmet program request was not blocked")
    print("draft_status =", draft["generation_status"])
    print("draft_geometry_authority =", draft.get("geometry_authority"))
    print("complete_status =", complete["generation_status"])
    print("complete_parking_count =", len(complete.get("parking", [])))
    print("validation_status =", validated["validation_status"])
    print("geometry_valid =", validated["report"].geometry_valid)
    print("requirements_valid =", validated["report"].requirements_valid)
    print("vastu_score =", validated["scorecard"].vastu_score)
    print("report_bundle = pass")
    print("impossible_status =", impossible["generation_status"])
    print("impossible_reason =", impossible["blocked_reason"])
    print("official_scoring = false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
