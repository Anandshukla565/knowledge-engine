from pathlib import Path

from knowledge_engine.intake.confirmation import create_confirmation_package
from knowledge_engine.intake.json_adapter import load_floor_plan


def test_json_adapter_and_confirmation_are_local_boundaries(tmp_path):
    sample = Path(__file__).parents[1] / "samples" / "valid_minimal_plan.json"
    raw, plan, issues = load_floor_plan(sample)

    assert raw is not None
    assert plan is not None
    assert issues == []

    package_path = create_confirmation_package(sample, tmp_path / "confirmation")

    assert package_path.exists()
    assert package_path.with_suffix(".md").exists()
