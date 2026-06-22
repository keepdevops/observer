"""API gateway: roster-derived status + loud 503 when the bus is down (no broker)."""
import asyncio

from gateway.app import Gateway
from gateway.bus_proxy import BusProxy


class _RosterBus:
    def __init__(self, components):
        self._components = components

    async def request(self, subject, payload, timeout=0):
        return {"components": self._components}


class _DownBus:
    async def request(self, subject, payload, timeout=0):
        raise ConnectionError("no responders")


class _Req:  # minimal stand-in for an aiohttp request
    pass


def test_version_is_static_and_versioned():
    out = asyncio.run(BusProxy(_DownBus()).version())   # version never touches the bus
    assert out["service"] == "observer-gateway"
    assert "schema_version" in out


def test_status_derives_from_roster():
    bus = _RosterBus([
        {"component_id": "c1", "status": "online",
         "info": {"name": "programmer", "engine": "llama", "server_group": "g1"}},
        {"component_id": "c2", "status": "online",
         "info": {"name": "echo-fast", "engine": "echo"}},
    ])
    out = asyncio.run(BusProxy(bus).swarm_status())
    assert out["online"] == 2
    assert [c["name"] for c in out["components"]] == ["echo-fast", "programmer"]  # sorted


def test_status_returns_503_when_bus_down():
    gw = Gateway(BusProxy(_DownBus()))
    resp = asyncio.run(gw.swarm_status(_Req()))
    assert resp.status == 503
