"""Entry point: run the history recorder (persists runs + alerts to .run/history.jsonl)."""
from __future__ import annotations

import asyncio
import logging

from bus.nats_bus import Bus
from recorder import HISTORY_PATH
from recorder.service import Recorder
from recorder.store import HistoryStore

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("run_recorder")


async def main() -> None:
    bus = Bus(name="recorder")
    await bus.connect()
    recorder = Recorder(bus, HistoryStore(HISTORY_PATH))
    await recorder.start()
    log.info("Recorder running. History -> %s", HISTORY_PATH)
    try:
        await asyncio.Event().wait()
    finally:
        await bus.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Recorder stopped.")
