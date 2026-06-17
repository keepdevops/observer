"""Hook up ALL cofiswarm agents as drop-in model components on the observer bus.

Reads every agent in `cofiswarm-agent-registry/data/agents/`, and for each starts a bus
model component backed by that agent's live local server (llama.cpp / MLX) via
`LlamaServerBackend`. The agents then appear in the Observer GUI roster and stream real
tokens. Additive and read-only: cofiswarm itself is untouched.

    python run_cofiswarm.py                 # all agents
    python run_cofiswarm.py --only programmer reviewer
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import socket
from pathlib import Path

from adapters.cofiswarm_model import ModelComponent, load_info
from adapters.llama_backend import LlamaServerBackend
from bus.nats_bus import Bus

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("run_cofiswarm")

AGENTS_DIR = Path("~/cofiswarm/repos/cofiswarm-agent-registry/data/agents").expanduser()
# Concurrent requests allowed per backing llama/MLX server (they serialize internally).
PER_SERVER_CONCURRENCY = 1


def _reachable(port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.3)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


async def main(only: list[str], skip_unreachable: bool) -> None:
    bus = Bus(name="cofiswarm-bridge")
    await bus.connect()

    gates: dict[int, asyncio.Semaphore] = {}  # one shared gate per backing server (port)
    components: list[ModelComponent] = []
    for path in sorted(AGENTS_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        name = data.get("name") or data.get("agent_id")
        if only and name not in only:
            continue
        port = data.get("port")
        if skip_unreachable and (not port or not _reachable(port)):
            log.warning("Skipping %s — backing server on port %s not reachable", name, port)
            continue

        info = load_info({"name": name, "agent_json": str(path)})
        gate = gates.setdefault(port, asyncio.Semaphore(PER_SERVER_CONCURRENCY))
        backend = LlamaServerBackend(
            base_url=f"http://127.0.0.1:{port}",
            system_prompt=data.get("system_prompt", ""),
            model=info.model or name,
            gate=gate,
        )
        component = ModelComponent(bus, info, backend, component_id=f"cofi-{name}")
        await component.start()
        components.append(component)

    log.info("Hooked up %d cofiswarm agents to the observer bus", len(components))
    try:
        await asyncio.Event().wait()
    finally:
        for component in components:
            await component.shutdown()
        await bus.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=[], help="limit to these agent names")
    ap.add_argument("--all-agents", action="store_true",
                    help="register agents even if their backing server is down")
    args = ap.parse_args()
    try:
        asyncio.run(main(args.only, skip_unreachable=not args.all_agents))
    except KeyboardInterrupt:
        log.info("cofiswarm bridge stopped.")
