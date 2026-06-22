"""Entry point: the registry component (agent/mode/role/topology catalog over the bus)."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

from bus.nats_bus import Bus
from components.registry import DEFAULT_AGENTS_DIR, DEFAULT_MODES_YAML, RegistryComponent

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("run_registry")


async def main(agents_dir: Path, modes_yaml: Path, servers: str) -> None:
    bus = Bus(servers=servers, name="registry")
    await bus.connect()
    comp = RegistryComponent(bus, agents_dir=agents_dir, modes_yaml=modes_yaml)
    await comp.start()
    log.info("registry online (agents_dir=%s, modes=%s)", agents_dir, modes_yaml)
    try:
        await asyncio.Event().wait()
    finally:
        await comp.shutdown()
        await bus.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--agents-dir",
                    default=os.environ.get("COFISWARM_AGENTS_DIR", str(DEFAULT_AGENTS_DIR)))
    ap.add_argument("--modes", default=str(DEFAULT_MODES_YAML))
    ap.add_argument("--nats", default="nats://127.0.0.1:4222")
    args = ap.parse_args()
    try:
        asyncio.run(main(Path(args.agents_dir).expanduser(),
                         Path(args.modes).expanduser(), args.nats))
    except KeyboardInterrupt:
        log.info("registry stopped.")
