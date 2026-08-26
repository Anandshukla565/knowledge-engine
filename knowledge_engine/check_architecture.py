"""Verify the standalone, production runtime boundary without changing state."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent

RUNTIME_FILES = (
    "apps/api/main.py",
    "apps/api/service.py",
    "apps/cli/entrypoint.py",
    "apps/cli/planner.py",
    "planning/draft_generator.py",
    "planning/compact_templates.py",
    "planning/geometry_model.py",
    "planning/geometry_solver.py",
    "planning/usability.py",
    "planning/validation_adapter.py",
    "planning/workflow.py",
    "validation/pipeline.py",
    "validation/geometry.py",
    "suggestions/engine.py",
    "reports/architect_report.py",
    "reports/svg_renderer.py",
    "ai/gemini_client.py",
    "ai/tools.py",
    "knowledge/scoring_gate.py",
    "infrastructure/file_storage.py",
)

EXCLUDED_LEGACY_MODULES = (
    "apps/cli/main.py", "apps/cli/architect_pilot.py", "apps/cli/orchestrator_legacy.py",
    "domain/project.py", "ai/prompt_parser.py", "ai/plan_draft_generator.py",
    "ai/review_assistant.py", "intake/pdf_adapter.py", "intake/dxf_adapter.py",
    "intake/revit_adapter.py", "validation/stacking.py", "suggestions/prioritization.py",
    "reports/pdf_renderer.py", "infrastructure/database.py", "infrastructure/audit_log.py",
    "infrastructure/background_jobs.py",
)

LEGACY_IMPORT_RE = re.compile(
    r"(?m)^\s*(?:from|import)\s+(?:phase1_validator|phase2_suggestions|scripts|config|architect_input_confirmation|core)(?:\.|\s|$)"
)


def main() -> int:
    failures: list[str] = []
    legacy_imports: list[str] = []
    for module in RUNTIME_FILES:
        if not (ROOT / module).is_file():
            failures.append(f"missing runtime module: {module}")
    for module in EXCLUDED_LEGACY_MODULES:
        if (ROOT / module).exists():
            failures.append(f"legacy module still shipped: {module}")
    for path in ROOT.rglob("*.py"):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        if LEGACY_IMPORT_RE.search(path.read_text(encoding="utf-8-sig")):
            legacy_imports.append(path.relative_to(ROOT).as_posix())

    ready = not failures and not legacy_imports
    print("architecture_mirror_status =", "PASS" if ready else "FAIL")
    print("runtime_files =", len(RUNTIME_FILES))
    print("excluded_legacy_modules =", len(EXCLUDED_LEGACY_MODULES))
    print("legacy_runtime_dependents =", len(legacy_imports))
    print("standalone_package_status =", "READY" if ready else "NOT_READY")
    for item in failures:
        print("ERROR:", item)
    if legacy_imports:
        print("legacy_runtime_examples =", ", ".join(legacy_imports[:8]))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
