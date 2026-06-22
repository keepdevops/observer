"""Entry point: the model-lifecycle component (convert + vllm.start over the bus)."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os

from bus.nats_bus import Bus
from components.lifecycle import LifecycleComponent

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("run_lifecycle")


async def main(python: str | None, servers: str) -> None:
    bus = Bus(servers=servers, name="lifecycle")
    await bus.connect()
    comp = LifecycleComponent(bus, python=python)
    await comp.start()
    log.info("lifecycle online")
    try:
        await asyncio.Event().wait()
    finally:
        await comp.shutdown()
        await bus.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", default=os.environ.get("MATRIX_MLX_PYTHON"),
                    help="python used to launch convert/vllm jobs (default: mlx-env)")
    ap.add_argument("--nats", default="nats://127.0.0.1:4222")
    args = ap.parse_args()
    try:
        asyncio.run(main(args.python, args.nats))
    except KeyboardInterrupt:
        log.info("lifecycle stopped.")
