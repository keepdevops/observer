"""aiohttp app for the API gateway. Routes translate to the bus via BusProxy.

A bus-dependent route fails *loud* (503) if the broker/middle man is unreachable — never a
hang and never a silent 200. The Chain-of-Responsibility middleware (auth → rate-limit →
logging → router) runs at this single external surface (see `gateway/middleware.py`).
"""
from __future__ import annotations

import logging
import os

from aiohttp import web

from bus.nats_bus import Bus

from .bus_proxy import BusProxy
from .middleware import build_chain

logger = logging.getLogger(__name__)


class Gateway:
    def __init__(self, proxy: BusProxy) -> None:
        self._proxy = proxy

    async def _call(self, coro, label: str) -> web.Response:
        """Run a bus-backed call; fail loud as 503 if the bus/component is unreachable."""
        try:
            return web.json_response(await coro)
        except Exception:
            logger.error("%s: bus/component unavailable", label, exc_info=True)
            return web.json_response({"error": "bus unavailable"}, status=503)

    async def version(self, request: web.Request) -> web.Response:
        return web.json_response(await self._proxy.version())

    async def swarm_status(self, request: web.Request) -> web.Response:
        return await self._call(self._proxy.swarm_status(), "swarm_status")

    async def metrics(self, request: web.Request) -> web.Response:
        return await self._call(self._proxy.metrics(), "metrics")

    async def health_agents(self, request: web.Request) -> web.Response:
        return await self._call(self._proxy.health(), "health")

    async def config_get(self, request: web.Request) -> web.Response:
        return await self._call(self._proxy.config("get"), "config_get")

    async def config_set(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json body"}, status=400)
        return await self._call(
            self._proxy.config("set", body.get("key", ""), body.get("value")), "config_set")


def build_app(servers: str = "nats://127.0.0.1:4222") -> web.Application:
    bus = Bus(servers=servers, name="observer-gateway")
    gw = Gateway(BusProxy(bus))
    api_key = os.environ.get("OBSERVER_API_KEY", "")
    rate_limit = int(os.environ.get("OBSERVER_RATE_LIMIT", "0"))
    app = web.Application(middlewares=build_chain(api_key, rate_limit))
    app.router.add_get("/api/version", gw.version)
    app.router.add_get("/api/swarm/status", gw.swarm_status)
    app.router.add_get("/api/metrics", gw.metrics)
    app.router.add_get("/api/health/agents", gw.health_agents)
    app.router.add_get("/api/v1/config", gw.config_get)
    app.router.add_post("/api/v1/config", gw.config_set)

    async def _startup(_app):
        await bus.connect()

    async def _cleanup(_app):
        await bus.close()

    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)
    return app
