"""Pure, local-only service functions for the loopback API and agent tools."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from knowledge_engine.knowledge.scoring_gate import get_scoring_gate_status
from knowledge_engine.validation.pipeline import run_validation_pipeline


def validate_floor_plan_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate an inline JSON payload without accepting a caller-controlled path.

    Temporary files exist only for the duration of the request because the
    existing deterministic pipeline is file-oriented. No SQLite or rule state
    is read or modified by this function.
    """
    floor_plan = payload.get("floor_plan")
    if not isinstance(floor_plan, dict):
        raise ValueError("Request body must contain a floor_plan JSON object.")
    with tempfile.TemporaryDirectory(prefix="knowledge_engine_api_") as temp_dir:
        root = Path(temp_dir)
        input_path = root / "floor_plan.json"
        input_path.write_text(json.dumps(floor_plan), encoding="utf-8")
        result = run_validation_pipeline(input_path, root / "output", write_outputs=False)
        report = result["report"].model_dump(mode="json")
        scorecard = result["scorecard"].model_dump(mode="json")
    return {
        "api_scope": "local_only",
        "validation_report": report,
        "scorecard": scorecard,
        "report_markdown": result["markdown"],
        "plan_svg": result["svg_content"],
        "official_scoring_enabled": False,
        "official_vastu_scoring_used": False,
        "candidate_notes_used_for_scoring": False,
        "vastu_score": None,
    }


def api_capabilities() -> dict[str, Any]:
    gate = get_scoring_gate_status()
    return {
        "api_scope": "local_only_loopback",
        "operations": ["validate_floor_plan"],
        "accepts_file_paths": False,
        "writes_sqlite": False,
        "official_scoring_enabled": False,
        "vastu_score": None,
        "scoring_gate_reason": gate["reason"],
    }
