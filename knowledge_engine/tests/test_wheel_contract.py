from __future__ import annotations

import subprocess
import sys
import venv
import zipfile
from pathlib import Path


PACKAGE_ROOT = Path(__file__).parents[1]


def _run(command: list[str], *, cwd: Path, expected: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=180)
    assert result.returncode == expected, result.stdout + "\n" + result.stderr
    return result


def test_clean_wheel_install_is_authoritative(tmp_path):
    """A wheel installed outside the checkout must run without legacy imports."""
    wheel_dir = tmp_path / "wheel"
    _run(
        [sys.executable, "-m", "pip", "wheel", str(PACKAGE_ROOT), "--no-deps", "--wheel-dir", str(wheel_dir)],
        cwd=tmp_path,
    )
    wheel_path = next(wheel_dir.glob("knowledge_engine_runtime-*.whl"))
    with zipfile.ZipFile(wheel_path) as archive:
        names = archive.namelist()
    forbidden_suffixes = {
        "architect_pilot.py", "orchestrator_legacy.py", "pdf_adapter.py", "dxf_adapter.py",
        "revit_adapter.py", "pdf_renderer.py", "prioritization.py", "prompt_parser.py",
        "plan_draft_generator.py", "review_assistant.py", "background_jobs.py",
    }
    assert not any("/tests/" in name or name.rsplit("/", 1)[-1] in forbidden_suffixes for name in names)

    environment = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True, system_site_packages=True).create(environment)
    python = environment / "Scripts" / "python.exe"
    executable = environment / "Scripts" / "knowledge-engine.exe"
    _run([str(python), "-m", "pip", "install", "--no-deps", str(wheel_path)], cwd=tmp_path)
    _run([str(executable), "check"], cwd=tmp_path)

    sample = PACKAGE_ROOT / "samples" / "valid_minimal_plan.json"
    validation_dir = tmp_path / "validation"
    _run([str(executable), "validate", "--input", str(sample), "--out", str(validation_dir)], cwd=tmp_path)
    assert (validation_dir / "validation_report.json").is_file()

    suggestions_dir = tmp_path / "suggestions"
    _run(
        [
            str(executable), "suggest", "--validation-report", str(validation_dir / "validation_report.json"),
            "--scorecard", str(validation_dir / "scorecard.json"), "--phase1-input", str(sample),
            "--out", str(suggestions_dir),
        ],
        cwd=tmp_path,
    )
    assert (suggestions_dir / "architect_review_report.md").is_file()
    assert not (tmp_path / "architect_review_report.md").exists()

    plan_dir = tmp_path / "plan"
    planned = _run(
        [
            str(executable), "plan", "--width", "30", "--depth", "40", "--facing", "east",
            "--road-side", "east", "--bhk", "3", "--bathrooms", "3", "--parking", "--pooja",
            "--out", str(plan_dir),
        ],
        cwd=tmp_path,
        expected=0,
    )
    assert "architect_usable = True" in planned.stdout
    assert "ModuleNotFoundError" not in planned.stdout + planned.stderr
    assert (plan_dir / "planner_assessment.json").is_file()
