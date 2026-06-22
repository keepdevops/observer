"""Meta/observability contracts: metrics, agent health, config (S5).

Replaces the legacy `/api/metrics`, `/api/health/agents`, `/api/v1/config`. (`/api/version` and
`/api/swarm/status` stay gateway-derived from the roster — no component needed.)
"""
from __future__ import annotations

from typing import Any, Optional

from .base import Envelope, ServiceReply


class MetricsReply(ServiceReply):
    models: list[dict] = []     # per-model: runs/errors/avg_latency_ms/avg_tokens/avg_tps
    total_runs: int = 0


class HealthReply(ServiceReply):
    agents: list[dict] = []     # component_id / name / status / engine
    online: int = 0


class ConfigQuery(Envelope):
    op: str = "get"             # get | set
    key: str = ""               # dotted key for set; empty get returns whole config
    value: Optional[Any] = None


class ConfigReply(ServiceReply):
    config: dict = {}
