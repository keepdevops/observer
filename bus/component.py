"""ServiceComponent: the generic request/reply capability process.

Capability components (registry, slots, kvpool, launcher, data, ...) are *not* token
streamers — they answer one request envelope with one reply envelope. This base gives them
the shared plumbing so each capability is just a routing table of `subject -> handler`:

  - announce once on connect (kind="service", re-announce on `hello`);
  - validate the incoming envelope's schema major (reject loud, never misparse);
  - run the handler and reply, returning a `ServiceReply(ok=False, ...)` on any failure.

The streaming model lane keeps its own specialization (`adapters/cofiswarm_model.ModelComponent`).
"""
from __future__ import annotations

import logging
import uuid
from typing import Awaitable, Callable

from . import subjects as S
from .contracts.base import ServiceReply, major_supported
from .nats_bus import Bus

logger = logging.getLogger(__name__)

# A handler takes the decoded request dict and returns a reply envelope (an Envelope/ServiceReply).
Handler = Callable[[dict], Awaitable[object]]


class ServiceComponent:
    def __init__(self, bus: Bus, name: str, routes: dict[str, Handler],
                 kind: str = "service", component_id: str | None = None,
                 tags: list[str] | None = None):
        if not routes:
            raise ValueError("ServiceComponent needs at least one route")
        self._bus = bus
        self._name = name
        self._routes = routes
        self._kind = kind
        self._cid = component_id or f"{kind}-{name}-{uuid.uuid4().hex[:6]}"
        self._info = S.ModelInfo(name=name, engine=kind, tags=tags or [])
        self._primary = next(iter(routes))  # representative subject for announce

    async def start(self) -> None:
        for subject in self._routes:
            await self._bus.subscribe(subject, self._make_cb(subject))
        await self._bus.subscribe(S.HELLO, self._on_hello)
        await self._announce()
        logger.info("Service '%s' (%s) serving %s (id=%s)",
                    self._name, self._kind, list(self._routes), self._cid)

    def _make_cb(self, subject: str):
        handler = self._routes[subject]

        async def _cb(msg, data: dict) -> None:
            if not major_supported(data):
                ver = data.get("schema_version")
                logger.error("%s rejected %s: unsupported schema_version=%s", self._name, subject, ver)
                await self._respond(msg, ServiceReply(ok=False, error=f"unsupported schema_version {ver}"))
                return
            try:
                reply = await handler(data)
            except Exception:
                logger.error("%s handler failed on %s", self._name, subject, exc_info=True)
                await self._respond(msg, ServiceReply(ok=False, error="handler error"))
                return
            await self._respond(msg, reply)

        return _cb

    async def _respond(self, msg, reply) -> None:
        try:
            await msg.respond(reply.model_dump_json().encode())
        except Exception:
            logger.error("%s failed to respond", self._name, exc_info=True)

    async def _announce(self) -> None:
        await self._bus.publish(
            S.ANNOUNCE,
            S.Announce(component_id=self._cid, kind=self._kind, info=self._info,
                       infer_subject=self._primary),
        )

    async def _on_hello(self, msg, data: dict) -> None:
        await self._announce()  # middle man (re)started; re-announce (self-healing)

    async def shutdown(self) -> None:
        try:
            await self._bus.publish(S.GOODBYE, S.Goodbye(component_id=self._cid))
        except Exception:
            logger.error("Failed to publish goodbye for %s", self._cid, exc_info=True)
