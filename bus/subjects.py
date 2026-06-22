"""NATS subject names + message envelopes for the observer bus.

Subjects live in cofiswarm's `swarm.*` namespace (see
`cofiswarm-common/zmq/topics.yaml`) so this NATS bus slots alongside the existing
ZeroMQ topics instead of inventing a parallel namespace. Envelopes are Pydantic
models so every message is schema-validated at the broker boundary.

`ModelInfo` deliberately mirrors `cofiswarm-agent-registry/schemas/agent.json`, so an
existing cofiswarm agent file can be loaded straight into a model component.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from .contracts.base import Envelope

PREFIX = "swarm.observer"
ANNOUNCE = f"{PREFIX}.announce"          # component -> bus: I am here (async, once)
GOODBYE = f"{PREFIX}.goodbye"            # component -> bus: leaving gracefully
PRESENCE = f"{PREFIX}.presence"          # bus -> observers: online/offline updates
ALERT = f"{PREFIX}.alert"                # bus -> observers: needed-component-down alerts
REQUEST = f"{PREFIX}.request"            # observer -> middle man: inference request
ROSTER = f"{PREFIX}.roster"              # observer -> middle man: request current online roster
CANCEL = f"{PREFIX}.cancel"              # observer -> models: cancel an in-flight request
HELLO = f"{PREFIX}.hello"                # middle man -> components: re-announce yourselves

# --- Capability subject groups: the full monolith surface. The names are reserved here
# (mechanical mapping capability `foo` -> `.foo.*`); handlers land in later sprints. ---
ORCHESTRATE = f"{PREFIX}.orchestrate"            # + .<mode> (S1)
REGISTRY_AGENTS = f"{PREFIX}.registry.agents"    # (S1)
REGISTRY_MODES = f"{PREFIX}.registry.modes"
REGISTRY_ROLES = f"{PREFIX}.registry.roles"
REGISTRY_TOPOLOGY = f"{PREFIX}.registry.topology"
SLOTS_PRESSURE = f"{PREFIX}.slots.pressure"      # (S3)
SLOTS_EVICT = f"{PREFIX}.slots.evict"
KV_ADMIT = f"{PREFIX}.kv.admit"                  # (S3) token-budget gate
KV_EVALUATE = f"{PREFIX}.kv.evaluate"            # pressure -> auto_clear/proactive_evict
KV_POLICY = f"{PREFIX}.kv.policy"                # current policy config
LAUNCHER_CONFIGURE = f"{PREFIX}.launcher.configure"  # (S3)
LAUNCHER_STATUS = f"{PREFIX}.launcher.status"
LIFECYCLE_CONVERT = f"{PREFIX}.lifecycle.convert"    # (S2)
LIFECYCLE_VLLM_START = f"{PREFIX}.lifecycle.vllm.start"
DATA = f"{PREFIX}.data"                          # + .<resource> (S4)
TOOLS = f"{PREFIX}.tools"                        # + .<tool> (S4)
METRICS = f"{PREFIX}.metrics"                    # (S5)
HEALTH = f"{PREFIX}.health.agents"
CONFIG = f"{PREFIX}.config"
VERSION = f"{PREFIX}.version"
SWARM_STATUS = f"{PREFIX}.swarm.status"


def model_subject(name: str) -> str:
    """Request/reply subject a single model component subscribes to."""
    return f"{PREFIX}.model.{name}"


def tokens_subject(request_id: str) -> str:
    """Per-request fan-out subject the model streams Token chunks to."""
    return f"{PREFIX}.tokens.{request_id}"


def orchestrate_subject(mode: str) -> str:
    """Request subject an orchestrator component subscribes to per mode (S1)."""
    return f"{ORCHESTRATE}.{mode}"


def data_subject(resource: str) -> str:
    """Repository request subject for a data resource, e.g. memory/cache/history (S4)."""
    return f"{DATA}.{resource}"


def tool_subject(tool: str) -> str:
    """Request subject a tool-worker component subscribes to (S4)."""
    return f"{TOOLS}.{tool}"


class ModelInfo(BaseModel):
    """Mirrors the cofiswarm agent JSON shape (name/engine/backend/model/...)."""

    name: str = Field(min_length=1)
    engine: str = "echo"
    backend: Optional[str] = None
    model: Optional[str] = None
    context: int = Field(default=2048, gt=0)
    max_tokens: int = Field(default=512, gt=0)
    server_group: Optional[str] = None
    port: Optional[int] = None
    tags: list[str] = Field(default_factory=list)


class Announce(Envelope):
    component_id: str = Field(min_length=1)
    kind: str = "model"
    info: ModelInfo
    infer_subject: str = Field(min_length=1)


class Goodbye(Envelope):
    component_id: str = Field(min_length=1)
    reason: str = "shutdown"


class Status(str, Enum):
    online = "online"
    offline = "offline"


class Presence(Envelope):
    component_id: str
    status: Status
    reason: str = ""
    info: Optional[ModelInfo] = None


class AlertLevel(str, Enum):
    warning = "warning"
    error = "error"


class Alert(Envelope):
    level: AlertLevel = AlertLevel.error
    message: str
    component_id: Optional[str] = None
    request_id: Optional[str] = None


class InferRequest(Envelope):
    request_id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    prompt: str
    max_tokens: int = Field(default=512, gt=0)
    temperature: float = Field(default=0.2, ge=0.0)
    stream: bool = False
    # Optional conversation thread; when set, the model keeps multi-turn context.
    session_id: Optional[str] = None
    # Optional system-prompt override (role × model): run this role on the target's model.
    system: Optional[str] = None
    # Set by the middle man before dispatch: where the model should stream tokens.
    stream_subject: Optional[str] = None


class InferResponse(Envelope):
    request_id: str
    model: str
    ok: bool = True
    text: str = ""
    error: Optional[str] = None


class Token(Envelope):
    """One streamed chunk on `tokens_subject(request_id)`. Final chunk has done=True."""

    request_id: str
    model: str
    seq: int = 0
    text: str = ""
    done: bool = False
    error: Optional[str] = None
    # Populated on the final (done) token when the backend reports real usage.
    tokens: Optional[int] = None            # completion tokens generated
    tokens_per_sec: Optional[float] = None  # server-reported throughput, if available


class Cancel(Envelope):
    request_id: str = Field(min_length=1)


class Hello(Envelope):
    """Broadcast by the middle man on startup so components re-announce (self-healing)."""


class RosterRequest(Envelope):
    """Empty request body for the roster snapshot (late-joining observers)."""


class RosterReply(Envelope):
    components: list[Presence] = Field(default_factory=list)
