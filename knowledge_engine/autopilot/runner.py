"""Run a bounded, allowlisted task queue for the new architecture mirror."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parent
QUEUE_PATH = Path(__file__).with_name("approved_tasks.json")
LOG_PATH = Path(__file__).with_name("run_log.jsonl")

ALLOWED_TASKS = {
    "architecture_check": [sys.executable, "-B", "knowledge_engine/check_architecture.py"],
    "mirror_tests": [sys.executable, "-B", "-m", "pytest", "knowledge_engine/tests", "-q"],
    "planner_smoke": [sys.executable, "-B", "-m", "knowledge_engine.autopilot.smoke"],
}


def _load_queue() -> dict[str, Any]:
    return json.loads(QUEUE_PATH.read_text(encoding="utf-8"))


def _save_queue(queue: dict[str, Any]) -> None:
    QUEUE_PATH.write_text(json.dumps(queue, indent=2) + "\n", encoding="utf-8")


def _validate_task(task: dict[str, Any]) -> None:
    task_id = task.get("id")
    if task_id not in ALLOWED_TASKS:
        raise ValueError(f"task is not allowlisted: {task_id!r}")
    if task.get("status") not in {"PENDING", "RUNNING", "COMPLETE", "FAILED"}:
        raise ValueError(f"invalid task status for {task_id}: {task.get('status')!r}")


def _append_log(record: dict[str, Any]) -> None:
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")


def run_queue() -> int:
    queue = _load_queue()
    if queue.get("authorized") is not True:
        print("autopilot_status = BLOCKED")
        print("reason = approved_tasks.json is not authorized")
        return 2

    max_tasks = queue.get("max_tasks_per_run")
    if not isinstance(max_tasks, int) or not 1 <= max_tasks <= 10:
        print("autopilot_status = BLOCKED")
        print("reason = max_tasks_per_run must be between 1 and 10")
        return 2

    tasks = queue.get("tasks")
    if not isinstance(tasks, list):
        print("autopilot_status = BLOCKED")
        print("reason = tasks must be a list")
        return 2
    for task in tasks:
        _validate_task(task)

    pending = [task for task in tasks if task.get("status") == "PENDING"]
    selected = pending[:max_tasks]
    if not selected:
        print("autopilot_status = IDLE")
        print("next_action = add an explicitly authorized PENDING task")
        return 0

    print(f"autopilot_status = RUNNING")
    print(f"selected_tasks = {len(selected)}")
    for task in selected:
        task_id = task["id"]
        task["status"] = "RUNNING"
        _save_queue(queue)
        started = datetime.now(timezone.utc).isoformat()
        completed = False
        try:
            result = subprocess.run(
                ALLOWED_TASKS[task_id],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            completed = result.returncode == 0
            task["status"] = "COMPLETE" if completed else "FAILED"
            _append_log(
                {
                    "task_id": task_id,
                    "started_at": started,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "returncode": result.returncode,
                    "status": task["status"],
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
            )
            print(f"task = {task_id}; status = {task['status']}; returncode = {result.returncode}")
            if result.stdout:
                print(result.stdout.rstrip())
            if result.stderr:
                print(result.stderr.rstrip(), file=sys.stderr)
        finally:
            _save_queue(queue)
        if not completed and queue.get("stop_on_failure", True):
            print("autopilot_status = STOPPED_ON_FAILURE")
            return 1

    remaining = sum(task.get("status") == "PENDING" for task in tasks)
    print(f"autopilot_status = COMPLETE")
    print(f"pending_tasks = {remaining}")
    print("next_action = review run_log.jsonl before authorizing more work")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_queue())
