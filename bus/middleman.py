"""The middle man: central broker router + dependency-aware, no-heartbeat presence.

Wires the NATS `Bus` to the event-driven `Presence` registry. Everything flows over the
one broker. A *needed* model that is gone is detected at dispatch time via NATS
no-responders / timeout — at which point the middle man marks it offline and publishes a
dependency-aware ALERT to the observers. No polling, no heartbeat.
"""
from __future__ import annotations

import logging

from nats.aio.msg import Msg

from . import subjects as S
from .contracts.base import major_supported
from .nats_bus import Bus, NatsTimeoutError, NoRespondersError
from .presence import Presence

logger = logging.getLogger(__name__)


class MiddleMan:
    def __init__(self, bus: Bus, presence: Presence, dispatch_timeout: float = 180.0):
        self._bus = bus
        self._presence = presence
        self._timeout = dispatch_timeout

    async def start(self) -> None:
        await self._bus.subscribe(S.ANNOUNCE, self._on_announce)
        await self._bus.subscribe(S.GOODBYE, self._on_goodbye)
        await self._bus.subscribe(S.REQUEST, self._on_request)
        await self._bus.subscribe(S.ROSTER, self._on_roster)
        logger.info("MiddleMan listening on %s / %s / %s", S.ANNOUNCE, S.GOODBYE, S.REQUEST)
        # We may have (re)started after components; ask everyone to re-announce.
        await self._bus.publish(S.HELLO, S.Hello())
        logger.info("Broadcast hello — components will re-announce")

    async def _on_roster(self, msg: Msg, data: dict) -> None:
        reply = S.RosterReply(components=self._presence.snapshot())
        await msg.respond(reply.model_dump_json().encode())

    async def _on_announce(self, msg: Msg, data: dict) -> None:
        ann = self._presence.apply_announce(data)
        if ann is None:
            return
        await self._bus.publish(
            S.PRESENCE,
            S.Presence(component_id=ann.component_id, status=S.Status.online, info=ann.info),
        )

    async def _on_goodbye(self, msg: Msg, data: dict) -> None:
        cid = self._presence.apply_goodbye(data)
        if cid is None:
            return
        await self._bus.publish(
            S.PRESENCE,
            S.Presence(component_id=cid, status=S.Status.offline, reason="graceful"),
        )

    async def _on_request(self, msg: Msg, data: dict) -> None:
        if not major_supported(data):
            ver = data.get("schema_version")
            logger.error("Rejected request with unsupported schema_version=%s", ver)
            await self._alert(f"rejected request: unsupported schema_version {ver}",
                              request_id=data.get("request_id"))
            await self._reply_error(msg, data.get("request_id", "?"), data.get("model", "?"),
                                    f"unsupported schema_version {ver}")
            return
        try:
            req = S.InferRequest(**data)
        except Exception:
            logger.error("Rejected malformed request: %s", data, exc_info=True)
            await self._reply_error(msg, data.get("request_id", "?"), data.get("model", "?"),
                                    "invalid request schema")
            return

        subject = self._presence.model_subject(req.model)
        if subject is None:
            await self._alert(f"no model '{req.model}' registered", request_id=req.request_id)
            await self._fail_stream(req, "model not registered")
            await self._reply_error(msg, req.request_id, req.model, "model not registered")
            return

        if req.stream:
            # Tell the model where to stream; observers subscribe to the same subject.
            req.stream_subject = S.tokens_subject(req.request_id)

        try:
            result = await self._bus.request(subject, req, timeout=self._timeout)
        except NoRespondersError:
            # Genuinely absent — no subscriber on the subject. Deregister + alert.
            cid = self._presence.model_component(req.model)
            if cid:
                self._presence.mark_down(cid, "no responder")
                await self._bus.publish(
                    S.PRESENCE,
                    S.Presence(component_id=cid, status=S.Status.offline, reason="no responder"),
                )
            await self._alert(f"required model '{req.model}' is down (no responder)",
                              component_id=cid, request_id=req.request_id)
            await self._fail_stream(req, "required component down")
            await self._reply_error(msg, req.request_id, req.model, "required component down")
            return
        except NatsTimeoutError:
            # A subscriber exists but was too slow. It is NOT down — keep it registered.
            await self._alert(f"model '{req.model}' timed out (slow, still registered)",
                              component_id=self._presence.model_component(req.model),
                              request_id=req.request_id)
            await self._fail_stream(req, "model timed out")
            await self._reply_error(msg, req.request_id, req.model, "model timed out")
            return

        await msg.respond(S.InferResponse(**result).model_dump_json().encode())

    async def _fail_stream(self, req: S.InferRequest, error: str) -> None:
        """If this was a streaming request, close the token stream with a terminal error."""
        if not req.stream:
            return
        await self._bus.publish(
            S.tokens_subject(req.request_id),
            S.Token(request_id=req.request_id, model=req.model, done=True, error=error),
        )

    async def _alert(self, message: str, component_id=None, request_id=None) -> None:
        logger.warning("ALERT: %s", message)
        await self._bus.publish(
            S.ALERT, S.Alert(message=message, component_id=component_id, request_id=request_id)
        )

    async def _reply_error(self, msg: Msg, request_id: str, model: str, error: str) -> None:
        await msg.respond(
            S.InferResponse(request_id=request_id, model=model, ok=False, error=error)
            .model_dump_json().encode()
        )
