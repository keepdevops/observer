"""Append-only JSONL store for run + alert history.

One JSON record per line. Written by the recorder service, read by the GUI's /history
endpoint. Append + tail only — simple and crash-safe enough for local history.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


class HistoryStore:
    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, record: dict) -> None:
        try:
            line = json.dumps(record)
        except Exception:
            logger.error("Unserializable history record dropped: %r", record, exc_info=True)
            return
        try:
            with self._lock, self._path.open("a") as fh:
                fh.write(line + "\n")
        except Exception:
            logger.error("Failed to append to history at %s", self._path, exc_info=True)

    def tail(self, limit: int = 50) -> list[dict]:
        if not self._path.exists():
            return []
        try:
            lines = self._path.read_text().splitlines()
        except Exception:
            logger.error("Failed to read history at %s", self._path, exc_info=True)
            return []
        out: list[dict] = []
        for line in lines[-limit:]:
            try:
                out.append(json.loads(line))
            except Exception:
                logger.error("Skipping malformed history line", exc_info=True)
        return out
