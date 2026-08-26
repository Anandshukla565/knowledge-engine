"""Read-only source-packet lookup."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class SourceRepository:
    def __init__(self, source_packets_dir: str | Path | None = None):
        self.source_packets_dir = Path(source_packets_dir) if source_packets_dir is not None else None

    def list_packets(self) -> list[str]:
        if not self.source_packets_dir or not self.source_packets_dir.exists():
            return []
        return [path.stem for path in sorted(self.source_packets_dir.glob("packet_*.json"))]

    def get_packet(self, packet_id: str) -> dict[str, Any] | None:
        if not self.source_packets_dir:
            return None
        candidates = [
            self.source_packets_dir / f"{packet_id}.json",
            self.source_packets_dir / f"{packet_id.lower()}.json",
        ]
        for path in candidates:
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                return None
            return payload if isinstance(payload, dict) else None
        return None
