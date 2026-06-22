"""CLI facade over the bus — the same contract as the HTTP gateway, so they never drift.

    python -m gateway.cli version
    python -m gateway.cli status
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from bus.nats_bus import Bus

from .bus_proxy import BusProxy

logger = logging.getLogger(__name__)


CONNECT_TIMEOUT = 3.0


async def _run(cmd: str, servers: str) -> int:
    bus = Bus(servers=servers, name="observer-cli")
    # Short-lived facade: bound the connect so a down broker fails loud, never hangs.
    # (Long-lived services keep Bus's infinite reconnect for broker-bounce resilience.)
    try:
        await asyncio.wait_for(bus.connect(), timeout=CONNECT_TIMEOUT)
    except (Exception, asyncio.TimeoutError):
        logger.error("cannot reach bus at %s within %ss", servers, CONNECT_TIMEOUT, exc_info=True)
        return 2
    proxy = BusProxy(bus)
    try:
        out = await (proxy.version() if cmd == "version" else proxy.swarm_status())
        print(json.dumps(out, indent=2))
    except Exception:
        logger.error("command %r failed (bus/middle man down?)", cmd, exc_info=True)
        return 1
    finally:
        await bus.close()
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="observer")
    ap.add_argument("command", choices=["version", "status"])
    ap.add_argument("--nats", default="nats://127.0.0.1:4222")
    args = ap.parse_args(argv)
    return asyncio.run(_run(args.command, args.nats))


if __name__ == "__main__":
    sys.exit(main())
