"""Fail-closed, read-only scoring status for the standalone runtime."""

from __future__ import annotations

from typing import Any, Iterable

from .trust_policy import TrustDecision, classify_rule


DISABLED_REASON = "SQLite or external rules are not trusted for official scoring."


def get_scoring_gate_status(records: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    """Return diagnostics; this runtime never enables official scoring."""

    materialized = list(records)
    decisions: list[TrustDecision] = [classify_rule(record) for record in materialized]
    trusted = sum(decision.trusted for decision in decisions)
    return {
        "sqlite_rules_count": len(materialized),
        "trusted_official_rules_count": trusted,
        "official_scoring_enabled": False,
        "vastu_score": None,
        "reason": DISABLED_REASON,
        "untrusted_count": len(materialized) - trusted,
    }
