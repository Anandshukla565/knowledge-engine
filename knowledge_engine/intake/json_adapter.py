from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from knowledge_engine.domain.findings import ValidationIssue
from knowledge_engine.domain.floor_plan import FloorPlanSchema


def validate_floor_plan_data(data: dict) -> tuple[FloorPlanSchema | None, list[ValidationIssue]]:
    try:
        plan = FloorPlanSchema.model_validate(data)
    except ValidationError as exc:
        issues = [
            ValidationIssue(
                severity="error",
                code="schema_validation_error",
                message=error["msg"],
                location=".".join(str(part) for part in error["loc"]),
            )
            for error in exc.errors()
        ]
        return None, issues
    return plan, []


def load_floor_plan(path: str | Path) -> tuple[dict | None, FloorPlanSchema | None, list[ValidationIssue]]:
    input_path = Path(path)
    try:
        raw_data = json.loads(input_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None, None, [
            ValidationIssue(
                severity="error",
                code="input_not_found",
                message=f"Input file not found: {input_path}",
                location=str(input_path),
            )
        ]
    except json.JSONDecodeError as exc:
        return None, None, [
            ValidationIssue(
                severity="error",
                code="invalid_json",
                message=f"Invalid JSON: {exc.msg}",
                location=str(input_path),
            )
        ]
    plan, issues = validate_floor_plan_data(raw_data)
    return raw_data, plan, issues

