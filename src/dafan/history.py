"""Append-only history of finished operations (JSON lines)."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from .paths import user_data_dir

MAX_KEEP = 1000


class History:
    def __init__(self, path: Path | None = None):
        self.path = path or (user_data_dir() / "history.jsonl")
        self._lock = threading.Lock()

    def add(self, **record) -> None:
        record.setdefault("time", time.strftime("%Y-%m-%d %H:%M:%S"))
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            try:
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                if self.path.stat().st_size > 600_000:
                    self._trim()
            except OSError:
                pass  # history is best-effort; never break processing because of it

    def _trim(self) -> None:
        lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()[-MAX_KEEP:]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def load(self, limit: int = 300) -> list[dict]:
        with self._lock:
            try:
                lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                return []
        out = []
        for line in lines[-limit:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out[::-1]

    def clear(self) -> None:
        with self._lock:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
