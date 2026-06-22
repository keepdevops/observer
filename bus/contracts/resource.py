"""Resource-control contracts: slot-manager, kvpool, launcher (S3).

These are the **cross-language** contracts: the components are Go (their own standalone
repos), so the field names below mirror the existing Go JSON tags exactly
(`cofiswarm-kvpool/internal/policy`, `cofiswarm-slot-manager/internal/control`) — the
exported JSON Schema (`bus/schema/*.json`) is what the Go side validates against.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from .base import Envelope, ServiceReply


# --- kvpool (.kv.*) — admission / pressure-evaluation / policy --------------------
class KvAdmitRequest(Envelope):
    group: str = ""
    tokens: int = 0


class KvAdmitReply(ServiceReply):
    allowed: bool = True
    budget: int = 0
    reason: str = ""


class KvEvaluateRequest(Envelope):
    kv_pressure: float = 0.0
    query: str = ""


class KvEvaluateReply(ServiceReply):
    auto_clear: bool = False
    proactive_evict: bool = False
    reason: str = ""
    proactive_fraction: float = 0.0


class KvPolicyReply(ServiceReply):
    enabled: bool = False
    pressure_threshold: float = 0.0
    divergence_threshold: float = 0.0
    proactive_threshold: float = 0.0
    proactive_fraction: float = 0.0
    budgets: dict[str, int] = {}


# --- slot-manager (.slots.*) — pressure snapshot / eviction ----------------------
class PressureReading(BaseModel):
    endpoint_id: str = ""
    host: str = ""
    port: int = 0
    slots: int = 0
    usage: float = 0.0


class PressureReply(ServiceReply):
    readings: list[PressureReading] = []


class EvictRequest(Envelope):
    endpoint_id: str = ""   # empty == evict across all endpoints


class EvictReply(ServiceReply):
    cleared: int = 0        # slots cleared
    endpoints: int = 0      # endpoints acted on


# --- launcher (.launcher.*) — configure / status ---------------------------------
# Mirrors cofiswarm-launcher/internal/configure.Agent (JSON tags). Configure is async:
# the reply is `accepted` and per-port outcome is polled via .launcher.status.
class LaunchAgent(BaseModel):
    name: str
    model: str
    port: int
    backend: str = "llama"          # llama / mlx (falls back to engine)
    engine: str = ""
    context: int = 0
    max_tokens: int = 0
    draft_model: str = ""
    flash_attn: bool = False
    extra_args: list[str] = []
    kv_cache_type: str = ""         # KV-quant, e.g. "q4_0" or "q4_0,q8_0"
    rope_scaling: str = ""          # "linear" | "yarn"
    turbo_quant: bool = False


class ConfigureRequest(Envelope):
    agents: list[LaunchAgent] = []


class ConfiguredServer(BaseModel):
    port: int = 0
    model: str = ""
    agents: list[str] = []
    status: str = ""                # starting / ready / error


class ConfigureReply(ServiceReply):
    accepted: bool = False          # spawn kicked off; poll .launcher.status
    servers: list[ConfiguredServer] = []


class LauncherStatusReply(ServiceReply):
    active: bool = False
    ports: dict[str, str] = {}      # port -> state (starting/ready/error)
