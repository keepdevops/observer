"""Native orchestrator fan-out over a fake bus (no broker).

The fake bus simulates the registry (REGISTRY_MODES) and streaming agent model components:
a request to a model subject publishes that agent's tokens to the request's stream_subject,
then returns an InferResponse ack — mirroring adapters/cofiswarm_model.ModelComponent.
"""
import asyncio
import json

import pytest

from adapters.orchestrator import Orchestrator
from bus import subjects as S


class _FakeSub:
    def __init__(self, bus, subject):
        self._bus, self._subject = bus, subject

    async def unsubscribe(self):
        self._bus.subs.pop(self._subject, None)


class FakeBus:
    def __init__(self, modes, agent_tokens, tools=None):
        self.subs = {}
        self._modes = modes                # mode -> {"agents": [...]}
        self._agent_tokens = agent_tokens  # agent -> list[str]
        self._tools = tools or {}          # tool -> fn(args) -> output str
        self.seen = []                     # (agent, prompt) requests, in order

    async def subscribe(self, subject, cb):
        self.subs[subject] = cb
        return _FakeSub(self, subject)

    async def publish(self, subject, payload):
        cb = self.subs.get(subject)
        if cb:
            await cb(None, json.loads(payload.model_dump_json()))

    async def request(self, subject, payload, timeout=0):
        data = json.loads(payload.model_dump_json())
        if subject == S.REGISTRY_MODES:
            only = data.get("only") or list(self._modes)
            return {"modes": {m: self._modes[m] for m in only if m in self._modes}}
        if subject.startswith(S.TOOLS + "."):
            tool = subject.rsplit(".", 1)[-1]
            return {"schema_version": "1.0.0", "ok": True, "tool": tool,
                    "output": self._tools[tool](data.get("args", {}))}
        agent = subject.rsplit(".", 1)[-1]
        self.seen.append((agent, data["prompt"]))
        toks = self._agent_tokens.get(agent, [f"{agent} "])
        stream = data["stream_subject"]
        for seq, t in enumerate(toks):
            await self.publish(stream, S.Token(request_id=data["request_id"], model=agent,
                                               seq=seq, text=t))
        await self.publish(stream, S.Token(request_id=data["request_id"], model=agent,
                                           seq=len(toks), done=True, tokens=len(toks)))
        return {"request_id": data["request_id"], "model": agent, "ok": True,
                "text": "".join(toks)}


def _drain(orch):
    async def go():
        return "".join([p async for p in orch.generate_stream("Q", 64)])
    return asyncio.run(go())


def test_flat_marks_every_agent():
    bus = FakeBus({"flat": {"agents": ["a1", "a2"]}},
                  {"a1": ["A1 "], "a2": ["A2 "]})
    out = _drain(Orchestrator(bus, "flat"))
    assert "■ a1" in out and "■ a2" in out
    assert out.index("A1") < out.index("A2")


def test_pipeline_orders_stages_and_chains_output():
    bus = FakeBus({"pipeline": {"agents": ["a1", "a2"]}},
                  {"a1": ["A1"], "a2": ["A2"]})
    out = _drain(Orchestrator(bus, "pipeline"))
    assert out.index("[stage 1/2 · a1]") < out.index("[stage 2/2 · a2]")
    # stage 2 is fed stage 1's output as its prompt
    assert bus.seen[0] == ("a1", "Q")
    assert bus.seen[1] == ("a2", "A1")


def test_cascade_synthesizes_with_last_agent():
    bus = FakeBus({"cascade": {"agents": ["a1", "a2", "synth"]}},
                  {"a1": ["x "], "a2": ["y "], "synth": ["final"]})
    out = _drain(Orchestrator(bus, "cascade"))
    assert "synthesis (synth)" in out
    # the synthesizer's prompt carries both worker outputs
    synth_prompt = next(p for a, p in bus.seen if a == "synth")
    assert "## a1" in synth_prompt and "## a2" in synth_prompt


def test_router_only_hits_resolved_subset():
    bus = FakeBus({"router": {"agents": ["a1"]}},
                  {"a1": ["only "], "a2": ["nope "]})
    out = _drain(Orchestrator(bus, "router"))
    assert "a1" in out and "a2" not in [a for a, _ in bus.seen]


def test_no_agents_raises():
    bus = FakeBus({"flat": {"agents": []}}, {})
    with pytest.raises(RuntimeError):
        _drain(Orchestrator(bus, "flat"))


def test_tool_call_round_trips_and_folds_result():
    from components.tools import calc
    bus = FakeBus(
        {"flat": {"agents": ["a1"]}},
        {"a1": ['[[tool:calc {"expr":"2+2"}]] ']},
        tools={"calc": lambda args: str(calc.safe_eval(args["expr"]))},
    )
    out = _drain(Orchestrator(bus, "flat"))
    assert "[[tool:calc result]] 4" in out


def test_pipeline_feeds_tool_result_to_next_stage():
    from components.tools import calc
    bus = FakeBus(
        {"pipeline": {"agents": ["a1", "a2"]}},
        {"a1": ['[[tool:calc {"expr":"3*3"}]]'], "a2": ["done"]},
        tools={"calc": lambda args: str(calc.safe_eval(args["expr"]))},
    )
    _drain(Orchestrator(bus, "pipeline"))
    # stage 2's prompt carries stage 1's tool-augmented output (resume)
    a2_prompt = next(p for a, p in bus.seen if a == "a2")
    assert "[[tool:calc result]] 9" in a2_prompt
