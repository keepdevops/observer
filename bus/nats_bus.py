"""Thin async NATS wrapper — the single always-on broker ("middle man") transport.

This is novel piece #1 vs cofiswarm: instead of HTTP + ZeroMQ + SSE between services,
every component speaks pub/sub + request/reply over one NATS connection. `request()`
surfaces NATS *no-responders* / timeout so the caller can detect a missing component
without any heartbeat (see middleman.py).
"""
from __future__ import annotations

import json
import logging
from typing import Awaitable, Callable, Optional

import nats
from nats.aio.client import Client
from nats.aio.msg import Msg
from nats.errors import NoRespondersError
from nats.errors import TimeoutError as NatsTimeoutError

logger = logging.getLogger(__name__)

# Re-exported so callers (middleman) catch them without importing nats directly.
__all__ = ["Bus", "NoRespondersError", "NatsTimeoutError"]

Handler = Callable[[Msg, dict], Awaitable[None]]


class Bus:
    """Async NATS connection used by the middle man and every component."""

    def __init__(self, servers: str = "nats://127.0.0.1:4222", name: str = "observer"):
        self._servers = servers
        self._name = name
        self._nc: Optional[Client] = None

    async def connect(self) -> None:
        try:
            self._nc = await nats.connect(
                servers=[self._servers], name=self._name, max_reconnect_attempts=-1
            )
        except Exception:
            logger.error("Bus failed to connect to NATS at %s", self._servers, exc_info=True)
            raise
        logger.info("Bus connected to NATS at %s as %r", self._servers, self._name)

    @property
    def connected(self) -> bool:
        return self._nc is not None and self._nc.is_connected

    async def publish(self, subject: str, payload) -> None:
        assert self._nc is not None, "Bus.connect() not called"
        try:
            await self._nc.publish(subject, payload.model_dump_json().encode())
        except Exception:
            logger.error("Bus publish failed on %s", subject, exc_info=True)
            raise

    async def subscribe(self, subject: str, handler: Handler):
        assert self._nc is not None, "Bus.connect() not called"

        async def _cb(msg: Msg) -> None:
            try:
                data = json.loads(msg.data.decode())
            except Exception:
                logger.error("Bus dropped malformed message on %s", subject, exc_info=True)
                return
            try:
                await handler(msg, data)
            except Exception:
                logger.error("Bus handler raised on %s", subject, exc_info=True)

        return await self._nc.subscribe(subject, cb=_cb)

    async def request(self, subject: str, payload, timeout: float = 30.0) -> dict:
        """Request/reply. Raises NoRespondersError / NatsTimeoutError if no live responder."""
        assert self._nc is not None, "Bus.connect() not called"
        msg = await self._nc.request(subject, payload.model_dump_json().encode(), timeout=timeout)
        return json.loads(msg.data.decode())

    async def close(self) -> None:
        if self._nc is not None and self._nc.is_connected:
            try:
                await self._nc.drain()
            except Exception:
                logger.error("Bus close/drain failed", exc_info=True)
