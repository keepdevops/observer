"""Entry point: serve the Observer GUI (default http://127.0.0.1:8099)."""
from __future__ import annotations

import argparse
import logging

from aiohttp import web

from gui.server import build_app

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--nats", default="nats://127.0.0.1:4222")
    args = ap.parse_args()
    web.run_app(build_app(servers=args.nats), host=args.host, port=args.port)
