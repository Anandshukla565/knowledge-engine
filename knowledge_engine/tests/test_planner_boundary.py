from pathlib import Path
import json

from knowledge_engine.domain.floor_plan import FloorPlanSchema
from knowledge_engine.planning.usability import assess_architect_usability
from knowledge_engine.planning import (
    DraftPlanRequest,
    generate_draft_project_state,
    run_generated_draft,
    validate_generated_draft,
)
from knowledge_engine.apps.cli.planner import main as planner_cli_main
from knowledge_engine.apps.cli.entrypoint import main as runtime_cli_main
from knowledge_engine.validation.pipeline import build_validation_report


def test_planner_generates_provisional_three_bedroom_geometry():
    request = DraftPlanRequest(
        plot_width_ft=30,
        plot_depth_ft=40,
        facing="east",
        road_side="east",
        bhk=3,
        bathrooms=1,
        floors=1,
        project_name="30x40 East 3BHK",
    )

    result = generate_draft_project_state(request, seed=7)

    room_types = [str(room.get("type", "")).lower() for room in result["rooms"]]
    assert sum("bedroom" in room_type for room_type in room_types) >= 3
    assert result["geometry_authority"] == "CANONICAL_POLYGON"
    assert result["generation_status"] == "draft"
    assert result["generation_blockers"] == []


def test_supported_30x40_east_3bhk_template_is_architect_usable(tmp_path):
    request = DraftPlanRequest(
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

    result = run_generated_draft(request, tmp_path / "supported_template", seed=7)

    assert result["generation_status"] == "draft"
    assert result["validation_status"] == "pass"
    assert result["architect_usability"]["architect_usable"] is True
    assert result["scorecard"].vastu_score is None
    assert result["scorecard"].official_vastu_scoring_used is False
    assert result["template_id"] == "compact_30x40_east_3bhk_v1"


def test_planner_completes_supported_parking_pooja_and_bathrooms():
    request = DraftPlanRequest(
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

    result = generate_draft_project_state(request, seed=7)

    assert result["generation_status"] == "draft"
    assert result["generation_blockers"] == []
    assert len(result["parking"]) == 1
    assert any("pooja" in str(room["type"]).lower() for room in result["rooms"])
    assert sum("bathroom" in str(room["type"]).lower() for room in result["rooms"]) == 3
    repeated = generate_draft_project_state(request, seed=7)
    assert result == repeated
    assert result["generation_metadata"]["timestamp"] == "deterministic_template_v1"


def test_planner_blocks_program_that_cannot_fit():
    request = DraftPlanRequest(
        plot_width_ft=30,
        plot_depth_ft=40,
        facing="east",
        road_side="east",
        bhk=12,
        bathrooms=12,
        floors=1,
        requires_parking=True,
        requires_pooja=True,
    )

    result = generate_draft_project_state(request, seed=7)

    assert result["generation_status"] == "blocked"
    assert result["generation_blockers"]


def test_ready_planner_output_passes_validation_without_official_vastu_scoring():
    request = DraftPlanRequest(
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
    generated = generate_draft_project_state(request, seed=7)

    validated = validate_generated_draft(generated, request)

    assert validated["validation_skipped"] is False
    assert validated["validation_status"] == "pass"
    assert validated["report"].geometry_valid is True
    assert validated["report"].requirements_valid is True
    assert validated["scorecard"].vastu_score is None
    assert validated["scorecard"].official_vastu_scoring_used is False
    assert validated["scorecard"].candidate_notes_used_for_scoring is False


def test_blocked_planner_output_skips_validation():
    request = DraftPlanRequest(
        plot_width_ft=30,
        plot_depth_ft=40,
        facing="east",
        road_side="east",
        bhk=12,
        bathrooms=12,
        floors=1,
        requires_parking=True,
        requires_pooja=True,
    )
    generated = generate_draft_project_state(request, seed=7)

    validated = validate_generated_draft(generated, request)

    assert validated["validation_skipped"] is True
    assert validated["report"] is None
    assert validated["scorecard"] is None


def test_workflow_blocks_draft_that_is_not_architect_usable(tmp_path):
    request = DraftPlanRequest(
        plot_width_ft=30,
        plot_depth_ft=40,
        facing="east",
        road_side="north",
        bhk=3,
        bathrooms=3,
        floors=1,
        requires_parking=True,
        requires_pooja=True,
    )

    result = run_generated_draft(request, tmp_path / "draft_bundle", seed=7)

    assert result["generation_status"] == "blocked"
    assert result["validation_status"] == "pass"
    assert result["architect_usability"]["architect_usable"] is False
    assert result["blocked_reason"]
    assert result["validation_status"] == "pass"
    assert result["scorecard"].vastu_score is None
    for filename in (
        "planner_draft.json",
        "phase1_input.json",
        "validation/validation_report.json",
        "validation/scorecard.json",
        "validation/report.md",
        "validation/plan.svg",
        "planner_assessment.json",
    ):
        assert (tmp_path / "draft_bundle" / filename).exists()


def test_blocked_workflow_writes_no_normal_report_bundle(tmp_path):
    request = DraftPlanRequest(
        plot_width_ft=30,
        plot_depth_ft=40,
        facing="east",
        road_side="east",
        bhk=12,
        bathrooms=12,
        floors=1,
        requires_parking=True,
        requires_pooja=True,
    )

    result = run_generated_draft(request, tmp_path / "blocked_bundle", seed=7)

    assert result["generation_status"] == "blocked"
    assert result["validation_status"] == "skipped"
    assert not (tmp_path / "blocked_bundle").exists()


def test_planner_cli_blocks_non_usable_bundle_and_keeps_vastu_provisional(tmp_path, capsys):
    output_dir = tmp_path / "cli_bundle"

    exit_code = planner_cli_main(
        [
            "--width", "30",
            "--depth", "40",
            "--facing", "east",
            "--road-side", "north",
            "--bhk", "3",
            "--bathrooms", "3",
            "--parking",
            "--pooja",
            "--out", str(output_dir),
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 3
    assert "generation_status = blocked" in output
    assert "validation_status = pass" in output
    assert "architect_usable = false" in output
    assert (output_dir / "validation" / "report.md").exists()
    assert (output_dir / "validation" / "plan.svg").exists()
    assert (output_dir / "planner_assessment.json").exists()


def test_planner_cli_emits_usable_supported_template_bundle(tmp_path, capsys):
    output_dir = tmp_path / "usable_cli_bundle"

    exit_code = planner_cli_main(
        [
            "--width", "30",
            "--depth", "40",
            "--facing", "east",
            "--road-side", "east",
            "--bhk", "3",
            "--bathrooms", "3",
            "--parking",
            "--pooja",
            "--out", str(output_dir),
        ]
    )

    output = capsys.readouterr().out
    assessment = json.loads((output_dir / "planner_assessment.json").read_text(encoding="utf-8"))
    scorecard = json.loads((output_dir / "validation" / "scorecard.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert "generation_status = draft" in output
    assert "validation_status = pass" in output
    assert "architect_usable = True" in output
    assert assessment["architect_usable"] is True
    assert scorecard["vastu_score"] is None
    assert scorecard["official_vastu_scoring_used"] is False


def test_planner_cli_returns_blocked_for_unfit_program(tmp_path, capsys):
    exit_code = planner_cli_main(
        [
            "--width", "30",
            "--depth", "40",
            "--facing", "east",
            "--bhk", "12",
            "--bathrooms", "12",
            "--parking",
            "--pooja",
            "--out", str(tmp_path / "blocked_cli"),
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "generation_status = blocked" in output
    assert not (tmp_path / "blocked_cli").exists()


def test_architect_usability_accepts_complete_clean_plan(tmp_path):
    input_path = Path(__file__).parents[1] / "samples" / "demo_30x40_north_2bhk_clean.json"
    plan = FloorPlanSchema.model_validate_json(input_path.read_text(encoding="utf-8"))
    report, _, _, _ = build_validation_report(input_path, tmp_path / "validated")

    assessment = assess_architect_usability(plan, report)

    assert assessment.architect_usable is True
    assert assessment.blockers == ()


def test_architect_usability_gate_covers_openings_circulation_parking_and_stairs(tmp_path):
    source = Path(__file__).parents[1] / "samples" / "demo_30x40_north_2bhk_clean.json"
    baseline = json.loads(source.read_text(encoding="utf-8"))

    def assess_variant(name, mutate):
        payload = json.loads(json.dumps(baseline))
        mutate(payload)
        input_path = tmp_path / f"{name}.json"
        input_path.write_text(json.dumps(payload), encoding="utf-8")
        plan = FloorPlanSchema.model_validate(payload)
        report, _, _, _ = build_validation_report(input_path, tmp_path / name)
        return assess_architect_usability(plan, report)

    missing_door = assess_variant("door", lambda payload: payload["rooms"][0].update({"doors": []}))
    missing_vent = assess_variant("vent", lambda payload: payload["rooms"][1].update({"windows": [], "vents": []}))
    missing_circulation = assess_variant(
        "circulation", lambda payload: payload.update({"rooms": [room for room in payload["rooms"] if room["type"] != "circulation"]})
    )
    inaccessible_parking = assess_variant("parking", lambda payload: payload["parking"][0].update({"y": 25.0}))
    missing_stairs = assess_variant("stairs", lambda payload: payload["metadata"].update({"level_count": 2}))

    assert any("door information" in blocker for blocker in missing_door.blockers)
    assert any("ventilation information" in blocker for blocker in missing_vent.blockers)
    assert any("circulation space" in blocker for blocker in missing_circulation.blockers)
    assert any("road-side access" in blocker for blocker in inaccessible_parking.blockers)
    assert any("missing a staircase" in blocker for blocker in missing_stairs.blockers)
    for assessment in (missing_door, missing_vent, missing_circulation, inaccessible_parking, missing_stairs):
        assert assessment.architect_usable is False


def test_runtime_cli_validate_and_check(tmp_path, capsys):
    input_path = Path(__file__).parents[1] / "samples" / "valid_minimal_plan.json"
    output_dir = tmp_path / "validated"

    validate_exit = runtime_cli_main(
        ["validate", "--input", str(input_path), "--out", str(output_dir)]
    )
    validate_output = capsys.readouterr().out
    check_exit = runtime_cli_main(["check"])
    check_output = capsys.readouterr().out

    assert validate_exit == 0
    assert "validation_status = pass" in validate_output
    assert "vastu_score = None" in validate_output
    assert check_exit == 0
    assert "architecture_mirror_status = PASS" in check_output
    assert "standalone_package_status = READY" in check_output


def test_runtime_cli_help_is_success(capsys):
    exit_code = runtime_cli_main(["--help"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "plan" in output
    assert "validate" in output
