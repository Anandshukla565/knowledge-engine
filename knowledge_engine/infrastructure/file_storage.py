from __future__ import annotations

from pathlib import Path


def write_if_changed(path: str | Path, content: str | bytes, encoding: str = "utf-8") -> bool:
    """Write content only when the target does not already contain the same content."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    if isinstance(content, bytes):
        if target.exists() and target.read_bytes() == content:
            return False
        target.write_bytes(content)
        return True

    if target.exists():
        try:
            if target.read_text(encoding=encoding) == content:
                return False
        except UnicodeDecodeError:
            pass
    target.write_text(content, encoding=encoding)
    return True
