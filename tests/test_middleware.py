"""Gateway Chain-of-Responsibility middleware (no broker, no server)."""
import asyncio

from aiohttp import web

from gateway.middleware import (
    auth_middleware, build_chain, logging_middleware, rate_limit_middleware,
)


class _Req:
    def __init__(self, headers=None, remote="1.2.3.4"):
        self.headers = headers or {}
        self.remote = remote
        self.method = "GET"
        self.path = "/api/version"


async def _ok(_request):
    return web.json_response({"ok": True})


def _run(coro):
    return asyncio.run(coro)


def test_auth_disabled_when_no_key():
    mw = auth_middleware("")
    assert _run(mw(_Req(), _ok)).status == 200


def test_auth_rejects_missing_and_wrong_key():
    mw = auth_middleware("secret")
    assert _run(mw(_Req(), _ok)).status == 401
    assert _run(mw(_Req({"X-API-Key": "nope"}), _ok)).status == 401


def test_auth_accepts_bearer_and_header():
    mw = auth_middleware("secret")
    assert _run(mw(_Req({"Authorization": "Bearer secret"}), _ok)).status == 200
    assert _run(mw(_Req({"X-API-Key": "secret"}), _ok)).status == 200


def test_rate_limit_trips_after_n():
    mw = rate_limit_middleware(limit=2, window=100.0)
    req = _Req(remote="9.9.9.9")
    assert _run(mw(req, _ok)).status == 200
    assert _run(mw(req, _ok)).status == 200
    assert _run(mw(req, _ok)).status == 429   # third within window


def test_rate_limit_disabled_when_zero():
    mw = rate_limit_middleware(limit=0)
    for _ in range(5):
        assert _run(mw(_Req(), _ok)).status == 200


def test_logging_passes_through():
    assert _run(logging_middleware(_Req(), _ok)).status == 200


def test_chain_order_is_auth_ratelimit_logging():
    chain = build_chain(api_key="k", rate_limit=5)
    assert len(chain) == 3
    assert chain[-1] is logging_middleware  # router (aiohttp dispatch) follows
