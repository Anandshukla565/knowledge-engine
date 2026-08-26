from __future__ import annotations

from pathlib import Path

from knowledge_engine.suggestions.engine import build_suggestion_report


DEMO_ROOT = Path(__file__).resolve().parents[1] / "outputs" / "30x40_east_3bhk_supported_template"


def test_suggestion_engine_uses_new_boundary_and_stays_provisional() -> None:
    report = build_suggestion_report(
        DEMO_ROOT / "validation_report.json",
        DEMO_ROOT / "scorecard.json",
        DEMO_ROOT / "phase1_input.json",
    )

    assert report.suggestions
    assert report.vastu_score is None
    assert report.official_vastu_scoring_used is False
    assert report.candidate_notes_used_for_scoring is False
    assert all(item.auto_fix_available is False for item in report.suggestions)
