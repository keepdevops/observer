"""Entry point: run tool workers (each on .tools.<tool>).

    python run_tools.py                 # all tools
    python run_tools.py --only calc     # one tool
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from bus.nats_bus import Bus
from components.tools import calc, web
from components.tools.base import ToolWorker

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("run_tools")

TOOLS = {"calc": calc.run, "web": web.run}


async def main(only: list[str], servers: str) -> None:
    bus = Bus(servers=servers, name="tools")
    await bus.connect()
    workers: list[ToolWorker] = []
    for name, run in TOOLS.items():
        if only and name not in only:
            continue
        worker = ToolWorker(bus, name, run)
        await worker.start()
        workers.append(worker)
    log.info("tool workers online: %s", list(TOOLS) if not only else only)
    try:
        await asyncio.Event().wait()
    finally:
        for worker in workers:
            await worker.shutdown()
        await bus.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=[], help="limit to these tools")
    ap.add_argument("--nats", default="nats://127.0.0.1:4222")
    args = ap.parse_args()
    try:
        asyncio.run(main(args.only, args.nats))
    except KeyboardInterrupt:
        log.info("tool workers stopped.")
