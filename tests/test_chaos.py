"""Fault-isolation acceptance (S6): one component down ≠ system down (no broker).

Drives the real `Presence` + `MiddleMan` + `ServiceComponent` with fakes — the same code the
live stack runs — to lock in the design's resilience guarantees.
"""
import asyncio
import json

from nats.errors import NoRespondersError

from bus import subjects as S
from bus.component import ServiceComponent
from bus.contracts.base import ServiceReply
from bus.middleman import MiddleMan
from bus.presence import Presence


class RecordBus:
    def __init__(self, request_exc=None):
        self.published = []        # (subject, dict)
        self.subscribed = []
        self._exc = request_exc

    async def publish(self, subject, payload):
        self.published.append((subject, json.loads(payload.model_dump_json())))

    async def subscribe(self, subject, handler):
        self.subscribed.append(subject)

    async def request(self, subject, payload, timeout=0):
        if self._exc:
            raise self._exc
        return {}

    def subjects(self):
        return [s for s, _ in self.published]


class Msg:
    def __init__(self):
        self.reply = None

    async def respond(self, data):
        self.reply = json.loads(data.decode())


async def _noop(_data):
    return ServiceReply()


def _announce(p, name):
    p.apply_announce({"component_id": f"c-{name}", "kind": "model",
                      "info": {"name": name}, "infer_subject": S.model_subject(name)})


def test_single_component_down_does_not_take_system_down():
    p = Presence()
    _announce(p, "a")
    _announce(p, "b")
    p.mark_down("c-a", "no responder")
    assert p.model_subject("a") is None        # the downed one is gone
    assert p.model_subject("b") is not None     # the rest keep serving
    assert p.models() == ["b"]


def test_idle_goodbye_is_quiet_no_alert():
    p = Presence()
    _announce(p, "a")
    bus = RecordBus()
    asyncio.run(MiddleMan(bus, p)._on_goodbye(Msg(), {"component_id": "c-a", "reason": "shutdown"}))
    assert S.PRESENCE in bus.subjects()        # presence flips offline
    assert S.ALERT not in bus.subjects()        # ...but an idle drop is quiet


def test_needed_component_down_alerts_and_others_survive():
    p = Presence()
    _announce(p, "a")
    _announce(p, "b")
    bus = RecordBus(request_exc=NoRespondersError())
    asyncio.run(MiddleMan(bus, p)._on_request(Msg(), {"request_id": "r", "model": "a", "prompt": "x"}))
    assert S.ALERT in bus.subjects()           # needed component down → alert
    assert p.model_subject("a") is None         # evicted
    assert p.model_subject("b") is not None      # unrelated component unaffected


def test_hot_plug_component_joins_at_runtime():
    p = Presence()
    _announce(p, "a")
    assert p.model_subject("new") is None
    _announce(p, "new")                          # joins later, no restart
    assert p.model_subject("new") is not None


def test_broker_restart_broadcasts_hello():
    bus = RecordBus()
    asyncio.run(MiddleMan(bus, Presence()).start())
    assert S.HELLO in bus.subjects()            # restart → ask everyone to re-announce


def test_component_reannounces_on_hello():
    bus = RecordBus()
    comp = ServiceComponent(bus, "svc", {S.METRICS: _noop}, kind="svc")
    asyncio.run(comp._on_hello(None, {}))
    assert S.ANNOUNCE in bus.subjects()         # self-healing re-announce
