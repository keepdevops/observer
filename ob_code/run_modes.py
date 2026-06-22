"""Hook up cofiswarm orchestration MODES as drop-in components on the observer bus.

Default (S1+): each mode (flat / pipeline / cascade / router) runs NATIVELY — an
`Orchestrator` backend fans out to the agent model components directly over the bus, no
HTTP. Pass `--bridge` to fall back to the legacy cofiswarm-dispatch SSE path
(`DispatchModeBackend`, :8010) during cutover.

    python run_modes.py                      # native bus orchestration (all four modes)
    python run_modes.py --only pipeline      # one mode
    python run_modes.py --bridge             # legacy dispatch HTTP/SSE fallback
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import socket

from adapters.cofiswarm_model import ModelComponent
from adapters.orchestrator import Orchestrator
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


def _make_backend(bus: Bus, mode: str, bridge: bool):
    """Native bus orchestrator by default; legacy dispatch HTTP/SSE bridge with --bridge."""
    if bridge:
        from adapters.dispatch_backend import DispatchModeBackend
        return DispatchModeBackend(mode, base_url=f"http://{DISPATCH_HOST}:{DISPATCH_PORT}")
    return Orchestrator(bus, mode)


async def main(only: list[str], bridge: bool) -> None:
    if bridge and not _reachable(DISPATCH_HOST, DISPATCH_PORT):
        raise SystemExit(f"--bridge needs cofiswarm-dispatch on {DISPATCH_HOST}:{DISPATCH_PORT}")

    bus = Bus(name="cofiswarm-modes")
    await bus.connect()

    components: list[ModelComponent] = []
    for mode in MODES:
        if only and mode not in only:
            continue
        info = S.ModelInfo(name=f"swarm-{mode}", engine="cofiswarm-mode",
                           server_group="orchestrator", tags=["orchestration", mode])
        backend = _make_backend(bus, mode, bridge)
        component = ModelComponent(bus, info, backend, component_id=f"mode-{mode}")
        await component.start()
        components.append(component)

    log.info("Hooked up %d modes (%s)", len(components),
             "dispatch bridge" if bridge else "native bus orchestration")
    try:
        await asyncio.Event().wait()
    finally:
        for component in components:
            await component.shutdown()
        await bus.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=[], help="limit to these modes")
    ap.add_argument("--bridge", action="store_true",
                    help="use the legacy cofiswarm-dispatch HTTP/SSE path instead of native bus")
    args = ap.parse_args()
    try:
        asyncio.run(main(args.only, args.bridge))
    except KeyboardInterrupt:
        log.info("cofiswarm modes bridge stopped.")
