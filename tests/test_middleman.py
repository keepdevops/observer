"""Middle man dispatch-failure handling: no-responder vs timeout (the eviction bug).

Uses fakes — no broker. Locks in that a *slow* model (TimeoutError) stays registered,
while a *genuinely absent* one (NoRespondersError) is deregistered.
"""
import asyncio
import json

from nats.errors import NoRespondersError
from nats.errors import TimeoutError as NatsTimeoutError

from bus import subjects as S
from bus.middleman import MiddleMan
from bus.presence import Presence


class FakeBus:
    def __init__(self, exc):
        self._exc = exc
        self.published = []

    async def publish(self, subject, payload):
        self.published.append((subject, json.loads(payload.model_dump_json())))

    async def request(self, subject, payload, timeout=0):
        raise self._exc


class FakeMsg:
    def __init__(self):
        self.reply = None

    async def respond(self, data):
        self.reply = json.loads(data.decode())


def _presence_with_model():
    p = Presence()
    p.apply_announce({"component_id": "c1", "kind": "model",
                      "info": {"name": "m"}, "infer_subject": S.model_subject("m")})
    return p


def _dispatch(exc):
    presence = _presence_with_model()
    bus = FakeBus(exc)
    msg = FakeMsg()
    asyncio.run(MiddleMan(bus, presence)._on_request(
        msg, {"request_id": "r", "model": "m", "prompt": "x"}))
    return presence, bus, msg


def test_no_responder_deregisters_model():
    presence, bus, msg = _dispatch(NoRespondersError())
    assert presence.model_subject("m") is None              # evicted
    assert msg.reply["error"] == "required component down"
    assert any(subj == S.ALERT for subj, _ in bus.published)


def test_timeout_keeps_model_registered():
    presence, bus, msg = _dispatch(NatsTimeoutError())
    assert presence.model_subject("m") is not None          # slow != down
    assert msg.reply["error"] == "model timed out"
    assert any(subj == S.ALERT for subj, _ in bus.published)
