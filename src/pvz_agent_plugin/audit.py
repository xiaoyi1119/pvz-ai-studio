from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any


class AuditLog:
    """Thread-safe structured JSONL audit log with an in-memory event tail."""

    def __init__(self, root: str | Path = "logs", max_events: int = 1000) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.session_id = uuid.uuid4().hex[:12]
        self._seq = 0
        self._lock = threading.Lock()
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)

    def emit(self, category: str, event: str, *, level: str = "info", **data: Any) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            record = {
                "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "monotonic_ms": round(time.monotonic() * 1000),
                "session_id": self.session_id,
                "seq": self._seq,
                "level": level,
                "category": category,
                "event": event,
                # Compatibility aliases used by the intentionally simple frontend.
                "time": datetime.now().astimezone().isoformat(timespec="seconds"),
                "kind": category,
                "message": event,
                **data,
            }
            self._events.append(record)
            self._append(self.root / "events.jsonl", record)
            self._append(self.root / f"{category}.jsonl", record)
            return record

    def snapshot(self, state: dict[str, Any], *, reason: str) -> None:
        record = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "session_id": self.session_id,
            "reason": reason,
            "state": state,
        }
        with self._lock:
            self._append(self.root / "states.jsonl", record)

    def tail(self, limit: int = 100, category: str | None = None) -> list[dict[str, Any]]:
        size = max(1, min(int(limit), 1000))
        records = list(self._events)
        if category:
            records = [e for e in records if e.get("category") == category]
        return records[-size:]

    @staticmethod
    def _append(path: Path, record: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
