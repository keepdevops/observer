"""Run + alert history: observe the bus and persist completed runs to JSONL."""

from pathlib import Path

HISTORY_PATH = Path(__file__).resolve().parent.parent / ".run" / "history.jsonl"
