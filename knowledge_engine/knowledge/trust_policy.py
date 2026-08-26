"""Read-only trust classification for externally supplied rule records."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


TOOL_ONLY_MARKERS = (
    "openai codex",
    "recorded user-confirmed",
    "tool-recorded",
    "automation",
    "knowledge enhancement",
)
PROVISIONAL_MARKERS = (
    "provisional",
    "requires_digitization",
    "unverified",
    "quarantined",
    "internal_engineering_policy",
)


@dataclass(frozen=True)
class TrustDecision:
    trusted: bool
    reasons: tuple[str, ...]


def _as_bool(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def classify_rule(record: dict[str, Any]) -> TrustDecision:
    """Return a conservative trust decision without changing the record."""

    reasons: list[str] = []
    status = str(record.get("status") or "").strip().upper()
    source_status = str(record.get("source_status") or "").strip().lower()
    verified_by = str(record.get("verified_by") or "").strip()
    approved_by = str(record.get("approved_by") or "").strip()
    source_refs = _as_dict(record.get("source_refs"))

    if status not in {"APPROVED", "TRUSTED_OFFICIAL"}:
        reasons.append("status is not approved or trusted_official")
    if not _as_bool(record.get("expert_verified")):
        reasons.append("expert_verified is not true")
    if not verified_by:
        reasons.append("verified_by is missing")
    if any(marker in verified_by.lower() for marker in TOOL_ONLY_MARKERS):
        reasons.append("verified_by is tool-only provenance")
    if verified_by.lower().startswith("migration_"):
        reasons.append("verified_by is migration provenance")
    if not source_refs:
        reasons.append("source_refs are missing")
    if any(marker in source_status for marker in PROVISIONAL_MARKERS):
        reasons.append("source_status is provisional or quarantined")
    if not approved_by or not record.get("approved_at"):
        reasons.append("human approval provenance is incomplete")

    return TrustDecision(trusted=not reasons, reasons=tuple(reasons))
