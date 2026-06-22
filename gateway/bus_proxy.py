"""Translate external calls into bus messages — the gateway's only job.

Stateless: each call becomes a `swarm.observer.*` request/reply. `version` is static;
`swarm_status` derives from the middle man's roster, so "what's online" is answered without
any business logic here (the middle man stays the source of truth). New translations are
added per sprint as capabilities go bus-native.
"""
from __future__ import annotations

import logging

from bus import subjects as S
from bus.contracts.base import SCHEMA_VERSION, Envelope
from bus.contracts.meta import ConfigQuery

logger = logging.getLogger(__name__)
VERSION = "0.1.0"
ROSTER_TIMEOUT = 2.0
META_TIMEOUT = 5.0


class BusProxy:
    def __init__(self, bus) -> None:
        self._bus = bus

    async def version(self) -> dict:
        """Static identity — never touches the bus, so it answers even if the broker is down."""
        return {"service": "observer-gateway", "version": VERSION,
                "schema_version": SCHEMA_VERSION}

    async def swarm_status(self) -> dict:
        """Swarm status derived from the live roster (raises if the middle man is unreachable)."""
        reply = await self._bus.request(S.ROSTER, S.RosterRequest(), timeout=ROSTER_TIMEOUT)
        components = []
        for comp in reply.get("components", []):
            info = comp.get("info") or {}
            components.append({
                "component_id": comp.get("component_id"),
                "name": info.get("name") or comp.get("component_id"),
                "status": comp.get("status", "online"),
                "engine": info.get("engine", "?"),
                "server_group": info.get("server_group"),
            })
        components.sort(key=lambda c: c["name"] or "")
        return {"version": VERSION, "online": len(components), "components": components}

    async def metrics(self) -> dict:
        return await self._bus.request(S.METRICS, Envelope(), timeout=META_TIMEOUT)

    async def health(self) -> dict:
        return await self._bus.request(S.HEALTH, Envelope(), timeout=META_TIMEOUT)

    async def config(self, op: str = "get", key: str = "", value=None) -> dict:
        return await self._bus.request(
            S.CONFIG, ConfigQuery(op=op, key=key, value=value), timeout=META_TIMEOUT)
