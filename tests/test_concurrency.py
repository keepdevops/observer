"""Per-server concurrency gate: agents sharing a backing server must serialize."""
import asyncio

from adapters.llama_backend import LlamaServerBackend


def _backend_with_fake_post(gate, tracker):
    b = LlamaServerBackend("http://x", "", "m", gate=gate)

    async def fake_post(body):
        tracker["active"] += 1
        tracker["max"] = max(tracker["max"], tracker["active"])
        await asyncio.sleep(0.05)
        tracker["active"] -= 1
        if False:        # make this an async generator
            yield ""

    b._post_stream = fake_post
    return b


async def _drain(b):
    async for _ in b.generate_stream("p", 10):
        pass


def test_shared_gate_serializes():
    async def run():
        gate = asyncio.Semaphore(1)
        tracker = {"active": 0, "max": 0}
        b1 = _backend_with_fake_post(gate, tracker)
        b2 = _backend_with_fake_post(gate, tracker)
        await asyncio.gather(_drain(b1), _drain(b2))
        return tracker["max"]

    assert asyncio.run(run()) == 1  # never two in flight at once


def test_no_gate_allows_overlap():
    async def run():
        tracker = {"active": 0, "max": 0}
        b1 = _backend_with_fake_post(None, tracker)
        b2 = _backend_with_fake_post(None, tracker)
        await asyncio.gather(_drain(b1), _drain(b2))
        return tracker["max"]

    assert asyncio.run(run()) == 2  # both run concurrently without a gate
