"""Read-only access to externally governed rule records."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable


class RuleRepository:
    """Query an explicit SQLite database or approved JSON directory read-only."""

    def __init__(self, *, db_path: str | Path | None = None, approved_dir: str | Path | None = None):
        self.db_path = Path(db_path) if db_path is not None else None
        self.approved_dir = Path(approved_dir) if approved_dir is not None else None

    def list_rules(self, *, domain: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        if self.db_path is not None:
            return self._list_sqlite(domain=domain, limit=limit)
        return self._list_json(domain=domain, limit=limit)

    def get_rule(self, rule_id: str) -> dict[str, Any] | None:
        for record in self.list_rules(limit=1000):
            if str(record.get("rule_id") or "") == rule_id:
                return record
        return None

    def search(self, query: str, *, domain: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        words = {word.lower() for word in query.split() if len(word) > 2}
        ranked: list[tuple[float, dict[str, Any]]] = []
        for record in self.list_rules(domain=domain, limit=1000):
            content = " ".join(str(record.get(key) or "") for key in ("content", "what_layer", "tags"))
            score = len(words & {word.lower() for word in content.split()}) / len(words) if words else 0.0
            if score > 0:
                ranked.append((score, {**record, "retrieval_score": round(score, 4)}))
        ranked.sort(key=lambda item: (-item[0], str(item[1].get("rule_id") or "")))
        return [record for _, record in ranked[:limit]]

    def _list_sqlite(self, *, domain: str | None, limit: int) -> list[dict[str, Any]]:
        if not self.db_path or not self.db_path.exists():
            return []
        uri = f"file:{self.db_path.resolve().as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            connection.row_factory = sqlite3.Row
            columns = [row[1] for row in connection.execute("PRAGMA table_info(rules)").fetchall()]
            if not columns:
                return []
            if "domain" in columns and domain:
                rows = connection.execute("SELECT * FROM rules WHERE domain = ? LIMIT ?", (domain, limit)).fetchall()
            else:
                rows = connection.execute("SELECT * FROM rules LIMIT ?", (limit,)).fetchall()
            return [dict(row) for row in rows]

    def _list_json(self, *, domain: str | None, limit: int) -> list[dict[str, Any]]:
        if not self.approved_dir or not self.approved_dir.exists():
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(self.approved_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and (not domain or payload.get("domain") == domain):
                records.append(payload)
            if len(records) >= limit:
                break
        return records
