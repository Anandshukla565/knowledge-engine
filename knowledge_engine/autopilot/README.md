# Bounded Autopilot

This is a local, bounded runner for explicitly authorized checks inside the
curated architecture mirror. It is not an infinite loop and it does not read
or control another ChatGPT/Claude browser session.

## Run

From the repository root:

```text
python -B -m knowledge_engine.autopilot.runner
```

The queue is [approved_tasks.json](approved_tasks.json). It contains task IDs
only. The runner maps those IDs to a fixed command allowlist, runs at most
`max_tasks_per_run` tasks, records output in `run_log.jsonl`, and stops at the
first failure when `stop_on_failure` is true.

## Safety boundary

The runner does not approve rules, move files to approved, write SQLite, enable
official scoring, change source verification, run `rule_factory_loop.py`, or
execute arbitrary shell commands. It only runs the three bounded mirror checks
currently listed in the queue.

Completed tasks are not rerun automatically. Adding or resetting a task requires
an explicit queue edit, which is the authorization boundary for the next run.
