"""Gateway parity: the legacy /api/* surface is served and maps to the bus (no broker).

A full legacy-vs-gateway response diff needs captured legacy fixtures; here we lock in route
coverage + that each bus-backed route fails loud (503) when the bus is down, and that the
BusProxy targets the right subjects.
"""
import asyncio

from bus import subjects as S
from gateway.app import Gateway, build_app
from gateway.bus_proxy import BusProxy

# Legacy coordinator routes that must be answered by the gateway.
EXPECTED_ROUTES = {
    ("GET", "/api/version"),
    ("GET", "/api/swarm/status"),
    ("GET", "/api/metrics"),
    ("GET", "/api/health/agents"),
    ("GET", "/api/v1/config"),
    ("POST", "/api/v1/config"),
}


def test_gateway_exposes_expected_routes():
    app = build_app()
    have = {(r.method, r.resource.canonical) for r in app.router.routes()}
    missing = EXPECTED_ROUTES - have
    assert not missing, f"gateway missing routes: {missing}"


class _DownBus:
    async def request(self, subject, payload, timeout=0):
        raise ConnectionError("no responders")


class _Req:
    pass


def test_bus_backed_routes_fail_loud_503():
    gw = Gateway(BusProxy(_DownBus()))
    for handler in (gw.swarm_status, gw.metrics, gw.health_agents, gw.config_get):
        assert asyncio.run(handler(_Req())).status == 503


class _SpyBus:
    def __init__(self):
        self.subjects = []

    async def request(self, subject, payload, timeout=0):
        self.subjects.append(subject)
        return {"ok": True}


def test_proxy_targets_correct_subjects():
    bus = _SpyBus()
    p = BusProxy(bus)
    asyncio.run(p.metrics())
    asyncio.run(p.health())
    asyncio.run(p.config("get"))
    assert bus.subjects == [S.METRICS, S.HEALTH, S.CONFIG]
