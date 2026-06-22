"""Emit JSON Schema for every bus envelope into `bus/schema/*.json`.

The exported schema is the ONE cross-repo, cross-language contract: polyglot components
(the Go launcher, future Rust/JS) validate against it. Python Pydantic models remain the
source of truth — run this whenever an envelope changes.

    python -m bus.schema_export            # (re)write bus/schema/*.json
    python -m bus.schema_export --check    # CI: non-zero exit if on-disk schema is stale
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import logging
import pkgutil
import sys
from pathlib import Path

from pydantic import BaseModel

from . import contracts
from . import subjects as S
from .contracts.base import SCHEMA_VERSION

logger = logging.getLogger(__name__)
OUT = Path(__file__).resolve().parent / "schema"

# Core presence/infer envelopes live in subjects.py (plus the nested ModelInfo). Capability
# contracts live under bus/contracts/* and are auto-discovered, so the export never goes
# stale as later sprints add modules.
ENVELOPES = [
    S.ModelInfo, S.Announce, S.Goodbye, S.Presence, S.Alert, S.InferRequest,
    S.InferResponse, S.Token, S.Cancel, S.Hello, S.RosterRequest, S.RosterReply,
]


def _discover() -> dict[str, type[BaseModel]]:
    """All Pydantic models declared in bus/contracts/* submodules, keyed by class name."""
    found: dict[str, type[BaseModel]] = {}
    for mod in pkgutil.iter_modules(contracts.__path__):
        m = importlib.import_module(f"{contracts.__name__}.{mod.name}")
        for name, obj in inspect.getmembers(m, inspect.isclass):
            if issubclass(obj, BaseModel) and obj.__module__ == m.__name__:
                found[name] = obj
    return found


def render() -> dict[str, str]:
    """Map each envelope name to its canonical (stable-sorted) JSON Schema text."""
    models = {m.__name__: m for m in ENVELOPES}
    models.update(_discover())
    return {
        name: json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
        for name, model in sorted(models.items())
    }


def write(out: Path = OUT) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in render().items():
        path = out / f"{name}.json"
        path.write_text(text)
        written.append(path)
    (out / "VERSION").write_text(SCHEMA_VERSION + "\n")
    return written


def check(out: Path = OUT) -> bool:
    """True if on-disk schema matches the models (CI drift guard)."""
    ok = True
    for name, text in render().items():
        path = out / f"{name}.json"
        if not path.exists() or path.read_text() != text:
            logger.error("schema drift: %s is missing or out of date", path.name)
            ok = False
    return ok


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify on-disk schema is current")
    args = ap.parse_args(argv)
    if args.check:
        return 0 if check() else 1
    paths = write()
    logger.info("wrote %d schema files + VERSION to %s", len(paths), OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
