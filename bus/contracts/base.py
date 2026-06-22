"""Versioned envelope base for every bus message.

Every message on `swarm.observer.*` carries `schema_version` (semver). The middle man and
gateway reject envelopes whose MAJOR they don't support, so a contract change is a loud,
explicit break rather than a silent misparse. JSON Schema is emitted from these models
(`bus/schema_export.py`) so non-Python components validate against the same contract.

Back-compat: an *unversioned* message (no `schema_version`) is tolerated and treated as
current — existing components keep working until they adopt the field.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0.0"
SCHEMA_MAJOR = int(SCHEMA_VERSION.split(".", 1)[0])


class Envelope(BaseModel):
    """Mixin: stamps every message with the contract version."""

    schema_version: str = Field(default=SCHEMA_VERSION)


class ServiceReply(Envelope):
    """Base for capability replies: every reply carries ok/error so failures are explicit.

    Capability-specific replies (e.g. AgentsReply) inherit this and add their data fields.
    A ServiceComponent returns `ServiceReply(ok=False, error=...)` for rejected/failed calls.
    """

    ok: bool = True
    error: Optional[str] = None


def major_of(data: dict) -> Optional[int]:
    """MAJOR of an incoming envelope dict, or None if unversioned/unparseable."""
    v = data.get("schema_version")
    if not isinstance(v, str):
        return None
    try:
        return int(v.split(".", 1)[0])
    except ValueError:
        return None


def major_supported(data: dict) -> bool:
    """True if the envelope is unversioned (legacy, accepted) or matches our MAJOR."""
    m = major_of(data)
    return m is None or m == SCHEMA_MAJOR
