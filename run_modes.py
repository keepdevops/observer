"""Hook up cofiswarm orchestration MODES as drop-in components on the observer bus.

Registers flat / pipeline / cascade / router (from cofiswarm-dispatch) as bus components
named `swarm-<mode>`. Dispatching one runs the full multi-agent orchestration via
dispatch's SSE API and streams agent/stage/routing-marked tokens back over the bus.

    python run_modes.py                      # all four modes
    python run_modes.py --only pipeline router
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import socket

from adapters.cofiswarm_model import ModelComponent
from adapters.dispatch_backend import DispatchModeBackend
from bus import subjects as S
from bus.nats_bus import Bus

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("run_modes")

MODES = ["flat", "pipeline", "cascade", "router"]
DISPATCH_HOST, DISPATCH_PORT = "127.0.0.1", 8010


def _reachable(host: str, port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.4)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


async def main(only: list[str]) -> None:
    if not _reachable(DISPATCH_HOST, DISPATCH_PORT):
        raise SystemExit(f"cofiswarm-dispatch not reachable on {DISPATCH_HOST}:{DISPATCH_PORT}")

    bus = Bus(name="cofiswarm-modes")
    await bus.connect()
    base_url = f"http://{DISPATCH_HOST}:{DISPATCH_PORT}"

    components: list[ModelComponent] = []
    for mode in MODES:
        if only and mode not in only:
            continue
        info = S.ModelInfo(name=f"swarm-{mode}", engine="cofiswarm-mode",
                           server_group="dispatch", tags=["orchestration", mode])
        backend = DispatchModeBackend(mode, base_url=base_url)
        component = ModelComponent(bus, info, backend, component_id=f"mode-{mode}")
        await component.start()
        components.append(component)

    log.info("Hooked up %d cofiswarm modes to the observer bus", len(components))
    try:
        await asyncio.Event().wait()
    finally:
        for component in components:
            await component.shutdown()
        await bus.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=[], help="limit to these modes")
    args = ap.parse_args()
    try:
        asyncio.run(main(args.only))
    except KeyboardInterrupt:
        log.info("cofiswarm modes bridge stopped.")
