from __future__ import annotations

import ast
from pathlib import Path

from knowledge_engine.domain import (
    FloorPlanSchema,
    Rule,
    Scorecard,
    Suggestion,
    ValidationIssue,
)


DOMAIN_DIR = Path(__file__).resolve().parents[1] / "domain"
LEGACY_ROOTS = {
    "phase1_validator",
    "phase2_suggestions",
    "scripts",
    "config",
    "architect_input_confirmation",
    "core",
}


def test_domain_exports_are_importable_without_legacy_package_imports() -> None:
    assert FloorPlanSchema
    assert Rule
    assert Scorecard
    assert Suggestion
    assert ValidationIssue


def test_domain_modules_have_no_legacy_imports() -> None:
    for path in DOMAIN_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name.split(".")[0] for alias in node.names}
                assert not names & LEGACY_ROOTS, path.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in LEGACY_ROOTS, path.name
