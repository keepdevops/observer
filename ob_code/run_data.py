"""Entry point: the data service (Repository tier — .data.* over the bus)."""
from __future__ import annotations

import argparse
import asyncio
import logging

from bus.nats_bus import Bus
from components.data_service import DataService

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("run_data")


async def main(servers: str) -> None:
    bus = Bus(servers=servers, name="data")
    await bus.connect()
    comp = DataService(bus)
    await comp.start()
    log.info("data service online (.data.*)")
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
        log.info("data service stopped.")
