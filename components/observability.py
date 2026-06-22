"""Observability component: metrics / agent-health / config over the bus (S5).

`.metrics` aggregates recorded runs (reuses `recorder/stats.aggregate`); `.health.agents`
derives from the live roster; `.config` is a JSON file get/set. Pure helpers (`agents_from_roster`)
are unit-tested without a broker.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from bus import subjects as S
from bus.component import ServiceComponent
from bus.contracts.meta import ConfigReply, HealthReply, MetricsReply
from recorder import HISTORY_PATH
from recorder.stats import aggregate
from recorder.store import HistoryStore

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = Path.home() / ".cofiswarm" / "observer-config.json"
ROSTER_TIMEOUT = 2.0


def agents_from_roster(components: list[dict]) -> list[dict]:
    """Map a roster snapshot to agent-health rows."""
    out = []
    for c in components:
        info = c.get("info") or {}
        out.append({
            "component_id": c.get("component_id"),
            "name": info.get("name") or c.get("component_id"),
            "status": c.get("status", "online"),
            "engine": info.get("engine", "?"),
        })
    out.sort(key=lambda a: a["name"] or "")
    return out


class Observability:
    def __init__(self, bus, config_path: Path = DEFAULT_CONFIG):
        self._bus = bus
        self._config_path = Path(config_path)
        self._store = HistoryStore(HISTORY_PATH)
        routes = {
            S.METRICS: self._on_metrics,
            S.HEALTH: self._on_health,
            S.CONFIG: self._on_config,
        }
        self._svc = ServiceComponent(bus, name="observability", routes=routes,
                                     kind="observability", tags=["meta"])

    async def start(self) -> None:
        await self._svc.start()

    async def shutdown(self) -> None:
        await self._svc.shutdown()

    async def _on_metrics(self, data: dict) -> MetricsReply:
        models = aggregate(self._store.tail(1000))
        return MetricsReply(models=models, total_runs=sum(m["runs"] for m in models))

    async def _on_health(self, data: dict) -> HealthReply:
        try:
            reply = await self._bus.request(S.ROSTER, S.RosterRequest(), timeout=ROSTER_TIMEOUT)
        except Exception:
            logger.error("observability: roster unavailable", exc_info=True)
            return HealthReply(ok=False, error="roster unavailable")
        agents = agents_from_roster(reply.get("components", []))
        return HealthReply(agents=agents, online=len(agents))

    def _read_config(self) -> dict:
        if not self._config_path.exists():
            return {}
        try:
            return json.loads(self._config_path.read_text())
        except Exception:
            logger.error("observability: bad config %s", self._config_path, exc_info=True)
            return {}

    async def _on_config(self, data: dict) -> ConfigReply:
        op = data.get("op", "get")
        cfg = self._read_config()
        if op == "get":
            return ConfigReply(config=cfg)
        if op == "set":
            key = data.get("key", "")
            if not key:
                return ConfigReply(ok=False, error="set requires 'key'")
            cfg[key] = data.get("value")
            try:
                self._config_path.parent.mkdir(parents=True, exist_ok=True)
                self._config_path.write_text(json.dumps(cfg, indent=2))
            except Exception:
                logger.error("observability: cannot write config", exc_info=True)
                return ConfigReply(ok=False, error="config not writable")
            return ConfigReply(config=cfg)
        return ConfigReply(ok=False, error=f"unsupported op '{op}'")
