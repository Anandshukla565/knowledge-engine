"""Official Vastu Rule Bundle — versioned, reviewed, gating source.

Official scoring reads ONLY rules from a separately-versioned bundle file,
NOT every `APPROVED` row in the live SQLite database.  This separates
"approved by engineer" (which can be provisional/pending digitization)
from "officially released" (which requires real evidence + expert review).

Schema:
    {
        "bundle_version": "2026-07-23.1",
        "released_at": "2026-07-23T18:00:00Z",
        "released_by": "human reviewer name",
        "approved_source_documents": [
            {
                "source_id": "...",
                "title": "...",
                "edition": "...",
                "page_locator": "...",
                "digitized_url": "https://...",
                "verified_by": "named human expert"
            }
        ],
        "rules": [
            {
                "rule_id": "...",
                "source_id": "...",
                "claim_summary": "...",
                "page_locator": "..."
            }
        ]
    }

The gate enables official scoring only if:
    1. KE_OFFICIAL_BUNDLE_PATH env var points to a valid bundle file.
    2. The bundle's rules reference rule_ids that pass the trust gate.
    3. CONTAINMENT_OVERRIDE in scoring_gate.py is False.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

BUNDLE_SCHEMA_VERSION = "2026-07-23.1"

MISSING_BUNDLE_REASON = (
    "Official Vastu scoring is observations-only: no official rule bundle is "
    "activated.  Set KE_OFFICIAL_BUNDLE_PATH to enable (after containment "
    "review and explicit human approval)."
)
EMPTY_BUNDLE_RULES_REASON = (
    "Official rule bundle is empty.  Official Vastu scoring is observations-only."
)
MISSING_BUNDLE_FILE_REASON = (
    "KE_OFFICIAL_BUNDLE_PATH points to a file that does not exist."
)
INVALID_BUNDLE_REASON = (
    "Official rule bundle file is not valid JSON or is missing required fields."
)


def load_official_rule_bundle(bundle_path: Path | str | None) -> dict[str, Any]:
    """Load the official rule bundle from disk.

    Returns a status dict (similar shape to scoring gate output) so that
    consumers can use a single dispatch path:
        {
            "bundle_path": str | None,
            "bundle_loaded": bool,
            "bundle_version": str | None,
            "released_at": str | None,
            "released_by": str | None,
            "official_rule_ids": [],
            "official_scoring_enabled": False,
            "reason": "...",
        }
    """
    if bundle_path is None:
        return {
            "bundle_path": None,
            "bundle_loaded": False,
            "bundle_version": None,
            "released_at": None,
            "released_by": None,
            "official_rule_ids": [],
            "official_scoring_enabled": False,
            "reason": MISSING_BUNDLE_REASON,
        }

    path = Path(bundle_path)
    result: dict[str, Any] = {
        "bundle_path": str(path),
        "bundle_loaded": False,
        "bundle_version": None,
        "released_at": None,
        "released_by": None,
        "official_rule_ids": [],
        "official_scoring_enabled": False,
        "reason": "",
    }

    if not path.exists():
        result["reason"] = MISSING_BUNDLE_FILE_REASON
        return result

    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (json.JSONDecodeError, OSError):
        result["reason"] = INVALID_BUNDLE_REASON
        return result

    if not isinstance(payload, dict):
        result["reason"] = INVALID_BUNDLE_REASON
        return result

    required = ("bundle_version", "released_at", "released_by", "rules")
    if not all(key in payload for key in required):
        result["reason"] = INVALID_BUNDLE_REASON
        return result

    rules = payload.get("rules") or []
    if not isinstance(rules, list):
        result["reason"] = INVALID_BUNDLE_REASON
        return result

    rule_ids: list[str] = []
    for entry in rules:
        if not isinstance(entry, dict):
            continue
        rid = entry.get("rule_id")
        if isinstance(rid, str) and rid:
            rule_ids.append(rid)

    result["bundle_loaded"] = True
    result["bundle_version"] = str(payload.get("bundle_version") or "")
    result["released_at"] = str(payload.get("released_at") or "")
    result["released_by"] = str(payload.get("released_by") or "")
    result["official_rule_ids"] = rule_ids
    if not rule_ids:
        result["reason"] = EMPTY_BUNDLE_RULES_REASON
    return result


def get_default_bundle_path() -> Path | None:
    """Return the default bundle path from env, or None if not set."""
    env = os.environ.get("KE_OFFICIAL_BUNDLE_PATH")
    if not env:
        return None
    return Path(env)