"""Top-level dispatcher for the curated Knowledge Engine runtime."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from knowledge_engine.check_architecture import main as architecture_check
from knowledge_engine.suggestions.engine import build_suggestion_report, write_architect_review_output, write_suggestion_outputs
from knowledge_engine.validation.pipeline import run_validation_pipeline


def _validate_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="knowledge_engine validate")
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    result = run_validation_pipeline(args.input, args.out, write_outputs=True)
    report = result["report"]
    scorecard = result["scorecard"]
    print(f"validation_status = {report.validation_status}")
    print(f"validation_report = {result['validation_report_path']}")
    print(f"scorecard = {result['scorecard_path']}")
    print(f"report_md = {result['markdown_path']}")
    print(f"plan_svg = {result['svg_path']}")
    print(f"vastu_score = {scorecard.vastu_score}")
    print("official_vastu_scoring_used = false")
    return 0 if report.validation_status == "pass" else 1


def _suggest_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="knowledge_engine suggest")
    parser.add_argument("--validation-report", required=True)
    parser.add_argument("--scorecard", required=True)
    parser.add_argument("--phase1-input")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    validation_report = Path(args.validation_report)
    scorecard = Path(args.scorecard)
    phase1_input = Path(args.phase1_input) if args.phase1_input else None
    output_dir = Path(args.out)
    report = build_suggestion_report(validation_report, scorecard, phase1_input)
    json_path, markdown_path = write_suggestion_outputs(report, output_dir)
    print(f"suggestion_report_json = {json_path}")
    print(f"suggestion_report_md = {markdown_path}")
    print(f"suggestion_count = {len(report.suggestions)}")
    print("official_scoring_used = false")
    print("auto_fix_available = false")
    if phase1_input is not None:
        architect_path = write_architect_review_output(
            report,
            phase1_input_path=phase1_input,
            validation_report_path=validation_report,
            scorecard_path=scorecard,
            output_path=output_dir / "architect_review_report.md",
        )
        print(f"architect_review_report = {architect_path}")
    return 0


def _help() -> None:
    print(
        "Usage: python -m knowledge_engine <command> [options]\n\n"
        "Commands:\n"
        "  plan      Generate and validate a provisional plan\n"
        "  validate  Validate an existing floor-plan JSON\n"
        "  suggest   Generate deterministic review suggestions\n"
        "  api       Run the local-only validation API\n"
        "  check     Check the architecture mirror\n"
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args in (["--help"], ["-h"]):
        _help()
        return 0
    command, command_args = args[0], args[1:]
    if command == "plan":
        from knowledge_engine.apps.cli.planner import main as planner_main

        return planner_main(command_args)
    if command == "validate":
        return _validate_main(command_args)
    if command == "suggest":
        return _suggest_main(command_args)
    if command == "check":
        return architecture_check()
    if command == "api":
        from knowledge_engine.apps.api.main import main as api_main

        return api_main(command_args)
    _help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
