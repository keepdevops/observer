"""Envelope versioning: default stamp + major gating at the middle man (no broker)."""
import asyncio
import json

from bus import subjects as S
from bus.contracts.base import SCHEMA_MAJOR, SCHEMA_VERSION, major_of, major_supported
from bus.middleman import MiddleMan
from bus.presence import Presence


def test_envelope_stamps_version():
    r = S.InferRequest(request_id="x", model="m", prompt="p")
    assert json.loads(r.model_dump_json())["schema_version"] == SCHEMA_VERSION


def test_major_of_parses_and_tolerates_garbage():
    assert major_of({"schema_version": "1.4.2"}) == 1
    assert major_of({"schema_version": "bad"}) is None
    assert major_of({}) is None


def test_major_supported_accepts_unversioned_and_current():
    assert major_supported({}) is True                                    # legacy, tolerated
    assert major_supported({"schema_version": SCHEMA_VERSION}) is True
    assert major_supported({"schema_version": f"{SCHEMA_MAJOR + 1}.0.0"}) is False


class _FakeMsg:
    def __init__(self):
        self.reply = None

    async def respond(self, data):
        self.reply = json.loads(data.decode())


class _FakeBus:
    def __init__(self):
        self.published = []

    async def publish(self, subject, payload):
        self.published.append((subject, json.loads(payload.model_dump_json())))

    async def request(self, subject, payload, timeout=0):
        raise AssertionError("a rejected request must never be dispatched")


def test_middleman_rejects_future_major_before_dispatch():
    bus, msg = _FakeBus(), _FakeMsg()
    asyncio.run(MiddleMan(bus, Presence())._on_request(
        msg, {"request_id": "r", "model": "m", "prompt": "x",
              "schema_version": f"{SCHEMA_MAJOR + 1}.0.0"}))
    assert msg.reply["ok"] is False
    assert "schema_version" in msg.reply["error"]
    assert any(subj == S.ALERT for subj, _ in bus.published)
