"""Verification client: act as the Observer GUI would.

Subscribes to presence + alert streams, then sends one inference request through the
middle man and prints the reply. Use it to watch a model go online, answer, and — if you
kill the model first — to see the dependency-aware ALERT instead of a hang.

    python probe.py --model echo-fast --prompt "hello"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import uuid

from bus import subjects as S
from bus.nats_bus import Bus

logging.basicConfig(level=logging.WARNING)


async def main(model: str, prompt: str, timeout: float, stream: bool) -> None:
    bus = Bus(name="probe")
    await bus.connect()

    async def show(msg, data):
        print(f"[{msg.subject}] {json.dumps(data)}")

    await bus.subscribe(S.PRESENCE, show)
    await bus.subscribe(S.ALERT, show)

    rid = uuid.uuid4().hex
    if stream:
        await _subscribe_tokens(bus, rid)
    req = S.InferRequest(request_id=rid, model=model, prompt=prompt, stream=stream)
    print(f"-> {'stream' if stream else 'request'} {rid} to model '{model}'")
    try:
        resp = await bus.request(S.REQUEST, req, timeout=timeout)
        if not stream:
            print("RESPONSE:\n" + json.dumps(resp, indent=2))
        else:
            print(f"\n[done] ok={resp.get('ok')} error={resp.get('error')}")
    except Exception as exc:
        print(f"request failed: {type(exc).__name__}: {exc}")
    finally:
        await asyncio.sleep(0.2)  # let trailing presence/alert messages print
        await bus.close()


async def _subscribe_tokens(bus, rid: str) -> None:
    """Print streamed tokens live as they arrive on the per-request subject."""

    async def on_token(msg, data):
        if data.get("error"):
            print(f"\n[stream error] {data['error']}")
        elif data.get("done"):
            pass  # terminal marker; final status printed by caller
        else:
            print(data.get("text", ""), end="", flush=True)

    await bus.subscribe(S.tokens_subject(rid), on_token)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="echo-fast")
    ap.add_argument("--prompt", default="hello from probe")
    ap.add_argument("--timeout", type=float, default=65.0)
    ap.add_argument("--stream", action="store_true", help="stream tokens live over the bus")
    args = ap.parse_args()
    asyncio.run(main(args.model, args.prompt, args.timeout, args.stream))
