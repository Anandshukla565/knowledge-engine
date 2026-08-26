from __future__ import annotations

from knowledge_engine.domain.findings import InputCompleteness, ValidationIssue
from knowledge_engine.domain.floor_plan import FloorPlanSchema


def _is_sha256(value: str | None) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdefABCDEF" for character in value
    )


def validate_input_completeness(plan: FloorPlanSchema) -> tuple[InputCompleteness, list[ValidationIssue]]:
    """Block geometry checks for unreviewed drawing-derived JSON.

    This validator intentionally knows nothing about PDF, OCR, DWG, or DXF.
    It only enforces the review boundary once a future intake flow has emitted
    structured JSON with provenance metadata.
    """

    provenance = plan.input_provenance
    if provenance is None:
        return InputCompleteness(), []

    if provenance.source_kind == "manual_json" and provenance.review_status == "not_applicable":
        return InputCompleteness(), []

    unresolved_items = list(provenance.unresolved_items)
    issues: list[ValidationIssue] = []
    if provenance.source_kind == "manual_json":
        issues.append(
            ValidationIssue(
                severity="error",
                code="input_review_incomplete",
                message=(
                    "Manual JSON input must use review_status='not_applicable'. "
                    "The supplied provenance metadata is contradictory and requires correction before validation."
                ),
                location="input_provenance.review_status",
            )
        )
    elif provenance.source_kind == "drawing_extraction_draft" or provenance.review_status in {"draft", "incomplete"}:
        issues.append(
            ValidationIssue(
                severity="error",
                code="input_review_incomplete",
                message=(
                    "Drawing-derived input is still provisional. Confirm plot geometry, room boundaries, "
                    "levels, orientation, and unresolved items before validation."
                ),
                location="input_provenance.review_status",
            )
        )
    elif provenance.source_kind == "reviewed_drawing" and provenance.review_status != "reviewed":
        issues.append(
            ValidationIssue(
                severity="error",
                code="input_review_incomplete",
                message="Reviewed drawing input requires review_status='reviewed' before validation.",
                location="input_provenance.review_status",
            )
        )

    if provenance.source_kind == "reviewed_drawing" and not (provenance.reviewed_by or "").strip():
        issues.append(
            ValidationIssue(
                severity="error",
                code="input_human_review_required",
                message="Reviewed drawing input requires the human reviewer to be identified before validation.",
                location="input_provenance.reviewed_by",
            )
        )

    if provenance.source_kind == "reviewed_drawing" and not (provenance.source_document_id or "").strip():
        issues.append(
            ValidationIssue(
                severity="error",
                code="input_human_review_required",
                message="Reviewed drawing input requires a source document ID before validation.",
                location="input_provenance.source_document_id",
            )
        )

    if provenance.source_kind == "reviewed_drawing" and not _is_sha256(provenance.source_document_checksum):
        issues.append(
            ValidationIssue(
                severity="error",
                code="input_human_review_required",
                message="Reviewed drawing input requires a valid source document SHA-256 checksum before validation.",
                location="input_provenance.source_document_checksum",
            )
        )

    if provenance.source_kind == "reviewed_drawing" and (
        not provenance.source_page_numbers or any(page < 1 for page in provenance.source_page_numbers)
    ):
        issues.append(
            ValidationIssue(
                severity="error",
                code="input_human_review_required",
                message="Reviewed drawing input requires at least one positive source page number before validation.",
                location="input_provenance.source_page_numbers",
            )
        )

    if provenance.source_kind == "reviewed_drawing" and not (provenance.reviewed_at or "").strip():
        issues.append(
            ValidationIssue(
                severity="error",
                code="input_human_review_required",
                message="Reviewed drawing input requires a review timestamp before validation.",
                location="input_provenance.reviewed_at",
            )
        )

    if unresolved_items:
        issues.append(
            ValidationIssue(
                severity="error",
                code="input_confirmation_required",
                message=(
                    "Input information requires confirmation before validation: "
                    + "; ".join(unresolved_items)
                    + "."
                ),
                location="input_provenance.unresolved_items",
            )
        )

    if issues:
        blocked_message = (
            "Manual JSON provenance metadata is contradictory and needs correction before validation."
            if provenance.source_kind == "manual_json"
            else "Geometry, practical, and candidate-note checks were not run because drawing-derived input needs human confirmation."
        )
        return (
            InputCompleteness(
                status="incomplete",
                source_kind=provenance.source_kind,
                review_status=provenance.review_status,
                geometry_validation_blocked=True,
                unresolved_items=unresolved_items,
                message=blocked_message,
            ),
            issues,
        )

    return (
        InputCompleteness(
            status="complete",
            source_kind=provenance.source_kind,
            review_status=provenance.review_status,
            geometry_validation_blocked=False,
            unresolved_items=[],
            message="Reviewed drawing-derived JSON is eligible for the existing structured validation pipeline.",
        ),
        [],
    )
