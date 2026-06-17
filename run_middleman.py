"""Entry point: start the always-on middle man (broker router + presence)."""
from __future__ import annotations

import asyncio
import logging

from bus.middleman import MiddleMan
from bus.nats_bus import Bus
from bus.presence import Presence

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("run_middleman")


async def main() -> None:
    bus = Bus(name="middleman")
    await bus.connect()
    mm = MiddleMan(bus, Presence())
    await mm.start()
    log.info("Middle man running. Components may join/leave at will. Ctrl-C to stop.")
    try:
        await asyncio.Event().wait()
    finally:
        await bus.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Middle man stopped.")
