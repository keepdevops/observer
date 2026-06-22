"""Data service: the Repository tier over the bus (S4).

Serves `.data.<resource>` with a uniform `DataQuery` -> `DataReply`. Real where it's cheap:
history reuses `recorder/store.py`; models/swarm-config read config files; logs tail `.run/*.log`;
memory/cache are in-process key/value stores. `rag` reports health (a real RAG backend plugs in
later). Unknown resource/op fail loud as `ok=False`.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml

from bus import subjects as S
from bus.component import ServiceComponent
from bus.contracts.data import DataReply
from recorder import HISTORY_PATH
from recorder.store import HistoryStore

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent.parent
MODELS_YAML = HERE / "config" / "models.yaml"
RUN_DIR = HERE / ".run"
RESOURCES = ["memory", "cache", "history", "rag", "logs", "swarm-config", "models"]


class KVRepo:
    """Trivial in-process key/value store (memory / cache)."""

    def __init__(self) -> None:
        self._d: dict[str, object] = {}

    def handle(self, q: dict) -> DataReply:
        op, key = q.get("op", "get"), q.get("key", "")
        if op == "get":
            return DataReply(ok=True, op=op, value=self._d.get(key))
        if op == "put":
            self._d[key] = q.get("value")
            return DataReply(ok=True, op=op, value=q.get("value"))
        if op == "delete":
            self._d.pop(key, None)
            return DataReply(ok=True, op=op)
        if op == "list":
            return DataReply(ok=True, op=op, items=sorted(self._d), count=len(self._d))
        return DataReply(ok=False, op=op, error=f"unsupported op '{op}'")


class DataService:
    def __init__(self, bus, models_yaml: Path = MODELS_YAML, run_dir: Path = RUN_DIR):
        self._models_yaml = Path(models_yaml)
        self._run_dir = Path(run_dir)
        self._history = HistoryStore(HISTORY_PATH)
        self._stores = {"memory": KVRepo(), "cache": KVRepo()}
        handlers = {
            "memory": self._stores["memory"].handle,
            "cache": self._stores["cache"].handle,
            "history": self._history_handler,
            "models": self._models_handler,
            "swarm-config": self._swarm_config_handler,
            "logs": self._logs_handler,
            "rag": self._rag_handler,
        }
        routes = {S.data_subject(r): self._wrap(r, h) for r, h in handlers.items()}
        self._svc = ServiceComponent(bus, name="data", routes=routes, kind="data", tags=["repository"])

    def _wrap(self, resource: str, handler):
        async def route(data: dict) -> DataReply:
            reply = handler(data)
            reply.resource = resource
            return reply
        return route

    async def start(self) -> None:
        await self._svc.start()

    async def shutdown(self) -> None:
        await self._svc.shutdown()

    def _history_handler(self, q: dict) -> DataReply:
        limit = max(1, min(500, int(q.get("params", {}).get("limit", 50))))
        items = self._history.tail(limit)
        return DataReply(ok=True, op="list", items=items, count=len(items))

    def _models_handler(self, q: dict) -> DataReply:
        try:
            cfg = yaml.safe_load(self._models_yaml.read_text()) or {}
        except Exception:
            logger.error("data: cannot read models.yaml %s", self._models_yaml, exc_info=True)
            return DataReply(ok=False, op="list", error="models config unavailable")
        models = cfg.get("models", [])
        return DataReply(ok=True, op="list", items=models, count=len(models))

    def _swarm_config_handler(self, q: dict) -> DataReply:
        path = os.environ.get("COFISWARM_SWARM_CONFIG", "")
        if not path or not Path(path).exists():
            return DataReply(ok=True, op="get", value=None, error="no swarm-config configured")
        try:
            import json
            return DataReply(ok=True, op="get", value=json.loads(Path(path).read_text()))
        except Exception:
            logger.error("data: bad swarm-config %s", path, exc_info=True)
            return DataReply(ok=False, op="get", error="swarm-config unreadable")

    def _logs_handler(self, q: dict) -> DataReply:
        name = q.get("key", "")
        if not name:
            return DataReply(ok=False, op="list", error="logs requires key=<service>")
        path = self._run_dir / f"{name}.log"
        if not path.exists():
            return DataReply(ok=False, op="list", error=f"no log for '{name}'")
        limit = max(1, min(1000, int(q.get("params", {}).get("limit", 100))))
        lines = path.read_text(errors="replace").splitlines()[-limit:]
        return DataReply(ok=True, op="list", items=lines, count=len(lines))

    def _rag_handler(self, q: dict) -> DataReply:
        url = os.environ.get("COFISWARM_RAG_URL", "")
        status = "configured" if url else "no-rag-configured"
        return DataReply(ok=True, op="health", value={"status": status, "url": url})
