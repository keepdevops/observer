"""Entry point: the observability component (.metrics / .health.agents / .config)."""
from __future__ import annotations

import argparse
import asyncio
import logging

from bus.nats_bus import Bus
from components.observability import Observability

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("run_observability")


async def main(servers: str) -> None:
    bus = Bus(servers=servers, name="observability")
    await bus.connect()
    comp = Observability(bus)
    await comp.start()
    log.info("observability online (.metrics/.health.agents/.config)")
    try:
        await asyncio.Event().wait()
    finally:
        await comp.shutdown()
        await bus.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--nats", default="nats://127.0.0.1:4222")
    args = ap.parse_args()
    try:
        asyncio.run(main(args.nats))
    except KeyboardInterrupt:
        log.info("observability stopped.")
