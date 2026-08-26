from __future__ import annotations

from knowledge_engine.domain.findings import OFFICIAL_VASTU_MESSAGE, Scorecard, ValidationReport
from knowledge_engine.validation.practical import PRACTICAL_CHECKS_MESSAGE, PRACTICAL_ISSUE_WEIGHTS

OFFICIAL_VASTU_DISABLED_MESSAGE = OFFICIAL_VASTU_MESSAGE


def _score_from_error_count(error_count: int, full_score: float = 100.0, penalty: float = 25.0) -> float:
    return max(0.0, full_score - (error_count * penalty))


def _practical_score(report: ValidationReport) -> float:
    penalty_total = 0
    for issue in report.issues:
        penalty_total += PRACTICAL_ISSUE_WEIGHTS.get(issue.code, 0)
    return max(0.0, 100.0 - penalty_total)


def build_scorecard(report: ValidationReport) -> Scorecard:
    if not report.schema_valid:
        return Scorecard(
            geometry_score=0.0,
            completeness_score=0.0,
            practical_score=0.0,
            vastu_score=None,
            vastu_excluded_from_overall=True,
            overall_score=0.0,
            official_vastu_message=OFFICIAL_VASTU_DISABLED_MESSAGE,
            official_vastu_scoring_message=OFFICIAL_VASTU_DISABLED_MESSAGE,
            official_vastu_scoring_used=False,
            candidate_notes_used_for_scoring=False,
            practical_checks_message=PRACTICAL_CHECKS_MESSAGE,
            provisional_notes=[
                "Schema validation failed before official scoring could run.",
                "Candidate rules are not imported for official scoring.",
            ],
        )

    geometry_errors = [
        issue
        for issue in report.issues
        if issue.severity == "error" and (issue.code.startswith("room_") or issue.code.startswith("parking_"))
    ]
    completeness_errors = [
        issue
        for issue in report.issues
        if issue.severity == "error" and issue.code.startswith("missing_required_")
    ]

    geometry_score = _score_from_error_count(len(geometry_errors), penalty=25.0)
    completeness_score = _score_from_error_count(len(completeness_errors), penalty=50.0)
    practical_score = _practical_score(report)
    overall_score = round((geometry_score + completeness_score + practical_score) / 3.0, 2)

    return Scorecard(
        geometry_score=round(geometry_score, 2),
        completeness_score=round(completeness_score, 2),
        practical_score=round(practical_score, 2),
        vastu_score=None,
        vastu_excluded_from_overall=True,
        overall_score=overall_score,
        official_vastu_message=OFFICIAL_VASTU_DISABLED_MESSAGE,
        official_vastu_scoring_message=OFFICIAL_VASTU_DISABLED_MESSAGE,
        official_vastu_scoring_used=False,
        candidate_notes_used_for_scoring=False,
        practical_checks_message=PRACTICAL_CHECKS_MESSAGE,
        provisional_notes=[
            "Candidate rules are not imported for official scoring.",
            "Any Vastu-related commentary is provisional only.",
        ],
    )
