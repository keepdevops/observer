"""Recorder: observes the bus and persists completed runs + alerts to history.

Subscribes to requests, the per-request token streams (`swarm.observer.tokens.>`
wildcard), and alerts. Reassembles each run (prompt -> streamed text -> final tokens) and
writes one record when it completes. Pure observer: it never responds to requests, so it
does not interfere with the middle man.
"""
from __future__ import annotations

import logging
import time

from bus import subjects as S
from bus.nats_bus import Bus

from .store import HistoryStore

logger = logging.getLogger(__name__)

TOKENS_WILDCARD = f"{S.PREFIX}.tokens.>"


class Recorder:
    def __init__(self, bus: Bus, store: HistoryStore):
        self._bus = bus
        self._store = store
        self._runs: dict[str, dict] = {}

    async def start(self) -> None:
        await self._bus.subscribe(S.REQUEST, self._on_request)
        await self._bus.subscribe(TOKENS_WILDCARD, self._on_token)
        await self._bus.subscribe(S.ALERT, self._on_alert)
        logger.info("Recorder observing requests, token streams, and alerts")

    async def _on_request(self, msg, data: dict) -> None:
        rid = data.get("request_id")
        if not rid:
            return
        self._runs[rid] = {
            "kind": "run", "request_id": rid, "model": data.get("model"),
            "prompt": data.get("prompt", ""), "started_at": time.time(),
            "text": "", "tokens": None, "tokens_per_sec": None, "status": "running",
        }

    async def _on_token(self, msg, data: dict) -> None:
        run = self._runs.get(data.get("request_id"))
        if not run:
            return
        if data.get("error"):
            run["status"] = "error"
            run["error"] = data["error"]
            self._finalize(run)
        elif data.get("done"):
            run["status"] = "done"
            run["tokens"] = data.get("tokens")
            run["tokens_per_sec"] = data.get("tokens_per_sec")
            self._finalize(run)
        else:
            run["text"] += data.get("text", "")

    def _finalize(self, run: dict) -> None:
        self._runs.pop(run["request_id"], None)
        run["ended_at"] = time.time()
        run["latency_ms"] = round((run["ended_at"] - run["started_at"]) * 1000)
        run["text"] = run["text"][:4000]  # cap stored body
        self._store.append(run)
        logger.info("recorded run %s model=%s status=%s tokens=%s",
                    run["request_id"][:8], run["model"], run["status"], run["tokens"])

    async def _on_alert(self, msg, data: dict) -> None:
        self._store.append({
            "kind": "alert", "at": time.time(), "message": data.get("message"),
            "component_id": data.get("component_id"), "request_id": data.get("request_id"),
        })
