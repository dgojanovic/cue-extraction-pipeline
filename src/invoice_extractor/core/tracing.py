"""JSONL tracing helpers for production-debuggable pipeline events."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def append_trace(path: str | Path | None, record: dict[str, Any]) -> None:
    """Append one JSON trace record without writing sensitive payloads to stdout."""

    if path is None:
        return

    trace_path = Path(path)
    trace_path.parent.mkdir(parents=True, exist_ok=True)

    with trace_path.open("a", encoding="utf-8") as trace_file:
        trace_file.write(json.dumps(record, ensure_ascii=False) + "\n")
