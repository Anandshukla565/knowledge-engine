"""Local CLI for provisional planner and validation bundles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from knowledge_engine.planning import DraftPlanRequest, run_generated_draft


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="knowledge-engine-plan",
        description="Generate and validate a provisional floor-plan draft.",
    )
    parser.add_argument("--width", type=float, required=True, help="Plot width in feet.")
    parser.add_argument("--depth", type=float, required=True, help="Plot depth in feet.")
    parser.add_argument("--facing", required=True, help="Plot facing direction.")
    parser.add_argument("--road-side", default=None, help="Road side; defaults to facing.")
    parser.add_argument("--bhk", type=int, default=2)
    parser.add_argument("--bathrooms", type=int, default=1)
    parser.add_argument("--kitchens", type=int, default=1)
    parser.add_argument("--floors", type=int, default=1)
    parser.add_argument("--parking", action="store_true", help="Require parking.")
    parser.add_argument("--pooja", action="store_true", help="Require a pooja room.")
    parser.add_argument("--project-name", default="Knowledge Engine Draft Plan")
    parser.add_argument("--source-prompt", default=None)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", required=True, help="Output directory for the provisional bundle.")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        request = DraftPlanRequest(
            plot_width_ft=args.width,
            plot_depth_ft=args.depth,
            facing=args.facing,
            road_side=args.road_side,
            bhk=args.bhk,
            bathrooms=args.bathrooms,
            kitchens=args.kitchens,
            floors=args.floors,
            requires_parking=args.parking,
            requires_pooja=args.pooja,
            project_name=args.project_name,
            source_prompt=args.source_prompt,
        )
    except ValueError as exc:
        parser.error(str(exc))

    result = run_generated_draft(request, args.out, seed=args.seed)
    payload = {
        "generation_status": result["generation_status"],
        "validation_status": result["validation_status"],
        "architect_usable": bool(result.get("architect_usability", {}).get("architect_usable", False)),
        "official_vastu_scoring_used": False,
        "vastu_score": None,
    }
    if result["generation_status"] == "blocked":
        payload["blocked_reason"] = result["blocked_reason"]
        if result.get("planner_assessment_path"):
            payload["planner_assessment"] = str(Path(result["planner_assessment_path"]).resolve())
        if args.json_output:
            print(json.dumps(payload, indent=2))
        else:
            print("generation_status = blocked")
            print(f"validation_status = {result['validation_status']}")
            print("architect_usable = false")
            print(f"blocked_reason = {result['blocked_reason']}")
        return 2 if result["validation_status"] == "skipped" else 3

    payload.update(
        {
            "output_dir": str(Path(result["output_dir"]).resolve()),
            "phase1_input": str(Path(result["phase1_input_path"]).resolve()),
            "validation_report": str(Path(result["validation_report_path"]).resolve()),
            "scorecard": str(Path(result["scorecard_path"]).resolve()),
            "report_md": str(Path(result["report_md_path"]).resolve()),
            "plan_svg": str(Path(result["plan_svg_path"]).resolve()),
        }
    )
    if args.json_output:
        print(json.dumps(payload, indent=2))
    else:
        for key, value in payload.items():
            print(f"{key} = {value}")
    return 0 if result["validation_status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
