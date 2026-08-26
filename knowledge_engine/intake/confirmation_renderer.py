"""Renderers for input confirmation packages."""

from __future__ import annotations

from typing import TYPE_CHECKING

from knowledge_engine.intake.confirmation_models import (
    CONFIRMATION_STATUS,
    EVIDENCE_TYPE,
    InputConfirmation,
)

if TYPE_CHECKING:
    pass


def _escape_markdown(value: object) -> str:
    """Escape data-derived text without changing renderer-owned Markdown."""
    text = str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", r"\n")
    text = text.replace("&", "&amp;")
    text = text.replace("\\", r"\\")
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    for character in ("`", "*", "_", "[", "]", "#", "|", "(", ")", "!"):
        text = text.replace(character, f"\\{character}")
    return text


def _fmt_value(value: object) -> str:
    """Format and escape a data-derived value for Markdown display."""
    if value is None:
        return "*Not supplied*"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, dict)):
        import json
        return _escape_markdown(json.dumps(value, indent=2, ensure_ascii=False))
    return _escape_markdown(value)


def _evidence_label(evidence_type: str) -> str:
    """Map evidence type to display label."""
    return {
        "EXPLICIT_INPUT": "Confirmed from input",
        "DERIVED_DETERMINISTICALLY": "Calculated from supplied geometry",
        "INFERRED_REQUIRES_CONFIRMATION": "Please confirm",
        "MISSING_INPUT": "Information not supplied",
    }.get(evidence_type, evidence_type)


def render_markdown(package: InputConfirmation) -> str:
    """Render an InputConfirmation as architect-facing Markdown."""
    lines: list[str] = []

    lines.append("# Input Confirmation Package")
    lines.append("")
    lines.append(f"**Source:** {_escape_markdown(package.source_input_path)}")
    lines.append("")
    lines.append(f"**Source SHA-256:** `{package.source_input_sha256}`")
    lines.append("")
    lines.append(f"**Product checkpoint:** {package.product_checkpoint_used}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 1. Preliminary-Input Boundary")
    lines.append("")
    lines.append("This package confirms what the system understood from the supplied structured input.")
    lines.append("It does not perform PDF/CAD extraction, does not calculate Vastu scores,")
    lines.append("does not approve compliance, construction, structural, MEP, or municipal readiness.")
    lines.append("")
    lines.append(f"**Review readiness:** {package.validation_readiness.result}")
    lines.append(f"**Reason:** {package.validation_readiness.reason}")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 2. What the System Understood")
    lines.append("")
    lines.append("### Confirmed Facts")
    lines.append("")
    lines.append("| Fact | Value | Evidence |")
    lines.append("| --- | --- | --- |")

    for fact in package.extracted_facts:
        if fact.evidence_type in ("EXPLICIT_INPUT", "DERIVED_DETERMINISTICALLY"):
            lines.append(f"| {fact.label} | {_fmt_value(fact.value)} | {_evidence_label(fact.evidence_type)} |")

    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 3. What Requires Architect Confirmation")
    lines.append("")
    lines.append("### Inferred or Uncertain Information")
    lines.append("")
    lines.append("| Fact | Inferred Value | Confidence |")
    lines.append("| --- | --- | --- |")

    if package.inferred_information:
        for inf in package.inferred_information:
            lines.append(f"| {inf.label} | {_fmt_value(inf.inferred_value)} | {inf.confidence} |")
    else:
        lines.append("*No inferred facts.*")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 4. Missing Information")
    lines.append("")
    lines.append("| Fact | Category | Blocking? |")
    lines.append("| --- | --- | --- |")

    if package.missing_information:
        for m in package.missing_information:
            lines.append(f"| {m.label} | {m.category} | {'Yes' if m.blocking else 'No'} |")
    else:
        lines.append("*No missing information.*")

    lines.append("")

    # Show architect responses if present
    if package.architect_responses:
        lines.append("---")
        lines.append("")
        lines.append("## 5. Corrected Information")
        lines.append("")
        lines.append("| Fact | Response | Corrected Value | Note |")
        lines.append("| --- | --- | --- | --- |")
        for resp in package.architect_responses:
            corrected = _fmt_value(resp.corrected_value) if resp.corrected_value is not None else "—"
            note = _escape_markdown(resp.note) if resp.note else "—"
            lines.append(f"| {resp.fact_id} | {resp.status} | {corrected} | {note} |")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 6. Blocking Items")
    lines.append("")

    if package.blocking_items:
        for bi in package.blocking_items:
            lines.append(f"- **{bi.label}**: {bi.reason}")
    else:
        lines.append("*No blocking items.*")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 7. Non-Blocking Limitations")
    lines.append("")

    if package.non_blocking_items:
        for nbi in package.non_blocking_items:
            lines.append(f"- **{nbi.label}**: {nbi.reason}")
    else:
        lines.append("*No non-blocking limitations.*")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 8. Review-Readiness Result")
    lines.append("")
    lines.append(f"**Result:** {package.validation_readiness.result}")
    lines.append("")
    lines.append(f"**Reason:** {package.validation_readiness.reason}")
    lines.append("")

    if package.validation_readiness.limitations:
        lines.append("**Limitations:**")
        for lim in package.validation_readiness.limitations:
            lines.append(f"- {lim}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 9. Clear Next Action")
    lines.append("")
    if package.validation_readiness.result == "NOT_READY_MISSING_REQUIRED_INPUT":
        lines.append("Provide the missing required information listed in Section 4 before proceeding to design review.")
    elif package.validation_readiness.result == "READY_WITH_LIMITATIONS":
        lines.append("Confirm the inferred or uncertain information listed in Section 3. The review may proceed with noted limitations.")
    else:
        lines.append("The input is ready for preliminary geometry and practical review. Proceed to design review with this confirmation package.")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Technical Appendix")
    lines.append("")
    lines.append("### Completeness Summary")
    lines.append("")
    lines.append(f"- Total facts: {package.completeness_summary.total_facts}")
    lines.append(f"- Missing facts: {package.completeness_summary.missing_count}")
    lines.append(f"- Inferred facts: {package.completeness_summary.inferred_count}")
    lines.append(f"- Blocking items: {package.completeness_summary.blocking_count}")
    lines.append(f"- Optional fields present: {package.completeness_summary.optional_fields_present}")
    lines.append("")
    lines.append("### Fact Details")
    lines.append("")
    lines.append("| ID | Label | Value | Evidence | Category |")
    lines.append("| --- | --- | --- | --- | --- |")
    for fact in package.extracted_facts:
        lines.append(f"| `{fact.fact_id}` | {fact.label} | {_fmt_value(fact.value)} | {_evidence_label(fact.evidence_type)} | {fact.category} |")

    if package.inferred_information:
        lines.append("")
        lines.append("### Inferred Fact Details")
        lines.append("")
        lines.append("| ID | Label | Inferred Value | Confidence |")
        lines.append("| --- | --- | --- | --- |")
        for inf in package.inferred_information:
            lines.append(f"| `{inf.fact_id}` | {inf.label} | {_fmt_value(inf.inferred_value)} | {inf.confidence} |")

    if package.missing_information:
        lines.append("")
        lines.append("### Missing Fact Details")
        lines.append("")
        lines.append("| ID | Label | Category | Blocking |")
        lines.append("| --- | --- | --- | --- |")
        for m in package.missing_information:
            lines.append(f"| `{m.fact_id}` | {m.label} | {m.category} | {'Yes' if m.blocking else 'No'} |")

    if package.architect_responses:
        lines.append("")
        lines.append("### Architect Response Details")
        lines.append("")
        lines.append("| Fact ID | Status | Corrected Value | Note |")
        lines.append("| --- | --- | --- | --- |")
        for resp in package.architect_responses:
            corrected = _fmt_value(resp.corrected_value) if resp.corrected_value is not None else "—"
            note = _escape_markdown(resp.note) if resp.note else "—"
            lines.append(f"| `{resp.fact_id}` | {resp.status} | {corrected} | {note} |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Generated by the architect input-confirmation workflow. This package confirms input completeness and does not constitute compliance assessment, construction readiness, structural approval, MEP validation, or any municipal, regulatory, or authority-level clearance.*")
    lines.append("")

    return "\n".join(lines)
