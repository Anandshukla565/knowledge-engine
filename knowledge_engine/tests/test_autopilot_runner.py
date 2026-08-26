import json
from pathlib import Path

from knowledge_engine.autopilot.runner import ALLOWED_TASKS, QUEUE_PATH


def test_autopilot_queue_is_bounded_and_allowlisted():
    queue = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    assert queue["authorized"] is True
    assert 1 <= queue["max_tasks_per_run"] <= 10
    task_ids = [task["id"] for task in queue["tasks"]]
    assert len(task_ids) == len(set(task_ids))
    assert set(task_ids) <= set(ALLOWED_TASKS)


def test_autopilot_commands_do_not_use_shell_or_mutation_commands():
    flattened = " ".join(" ".join(command) for command in ALLOWED_TASKS.values()).lower()
    assert "shell" not in flattened
    assert "sqlite" not in flattened
    assert "approve" not in flattened
    assert "rule_factory_loop" not in flattened
