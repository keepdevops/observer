"""Entry point: run ONE model component as its own standalone process.

    python run_model.py --name echo-fast
    python run_model.py --name programmer   # uses cofiswarm agent JSON if configured

To serve a real cofiswarm engine, build its InferenceBackend and wrap it:
    from adapters.cofiswarm_model import CofiBackendAdapter
    backend = CofiBackendAdapter(my_cofiswarm_backend)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

import yaml

from adapters.cofiswarm_model import EchoBackend, ModelComponent, load_info
from bus.nats_bus import Bus

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("run_model")


def load_specs(path: str) -> list[dict]:
    cfg = yaml.safe_load(Path(path).expanduser().read_text()) or {}
    return cfg.get("models", [])


def make_backend(spec: dict):
    """Select a backend by engine: mlx (spawn+serve), llama (connect), else echo."""
    engine = (spec.get("engine") or "echo").lower()
    if engine == "mlx":
        from adapters.mlx_backend import MLXServerBackend
        return MLXServerBackend(spec, manage=spec.get("manage", True))
    if engine == "llama":
        from adapters.llama_backend import LlamaServerBackend
        host, port = spec.get("host", "127.0.0.1"), spec["port"]
        return LlamaServerBackend(base_url=f"http://{host}:{port}",
                                  system_prompt=spec.get("system_prompt", ""),
                                  model=spec.get("model") or spec["name"])
    return EchoBackend(spec.get("name", "echo"))


async def run(spec: dict) -> None:
    info = load_info(spec)
    backend = make_backend(spec)
    bus = Bus(name=f"model-{info.name}")
    await bus.connect()
    # Managed backends (e.g. MLX) spawn their server first; serve anyway if not ready so
    # the failure surfaces loudly per-request (alert) rather than vanishing silently.
    if hasattr(backend, "ensure_ready") and not await backend.ensure_ready():
        log.error("backend for %s not ready; serving — requests will error loudly", info.name)
    component = ModelComponent(bus, info, backend)
    await component.start()
    try:
        await asyncio.Event().wait()
    finally:
        await component.shutdown()
        await bus.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Run a single drop-in model component.")
    ap.add_argument("--config", default="ob_code/models.yaml")
    ap.add_argument("--name", default=None, help="model name from config; default = first entry")
    args = ap.parse_args()

    specs = load_specs(args.config)
    if args.name:
        specs = [s for s in specs if s.get("name") == args.name]
    if not specs:
        raise SystemExit(f"No matching model in {args.config}")

    try:
        asyncio.run(run(specs[0]))
    except KeyboardInterrupt:
        log.info("Model component stopped.")


if __name__ == "__main__":
    main()
