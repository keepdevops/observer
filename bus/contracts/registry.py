"""Registry contracts: the agent / mode / role / topology catalog over the bus.

Replaces the cofiswarm-agent-registry HTTP surface (`/api/agents`, `/api/modes`,
`/modes/{n}/agents`, `/roles`). The registry component is the source of truth for which
agents exist and which agents each orchestration mode drives.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from .base import Envelope, ServiceReply


class AgentSpec(BaseModel):
    """One agent/role (mirrors a cofiswarm-agent-registry agent JSON, trimmed)."""

    name: str
    server_group: Optional[str] = None
    system_prompt: str = ""
    port: Optional[int] = None
    tags: list[str] = []


class ModeSpec(BaseModel):
    """One orchestration mode: its fan-out structure + the agents it drives."""

    structure: str = ""
    description: str = ""
    agents: list[str] = []


# --- agents --------------------------------------------------------------------
class AgentsQuery(Envelope):
    """Optional filter by name; empty `only` returns all agents."""

    only: list[str] = []


class AgentsReply(ServiceReply):
    agents: list[AgentSpec] = []


# --- modes ---------------------------------------------------------------------
class ModesQuery(Envelope):
    only: list[str] = []


class ModesReply(ServiceReply):
    modes: dict[str, ModeSpec] = {}


# --- roles (agents grouped for the role x model grid) --------------------------
class RolesReply(ServiceReply):
    roles: list[AgentSpec] = []
    groups: list[str] = []  # distinct server-groups


# --- topology (mode -> agent names) -------------------------------------------
class TopologyReply(ServiceReply):
    modes: dict[str, list[str]] = {}
