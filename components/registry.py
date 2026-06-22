"""Registry component: the agent / mode / role / topology catalog, served over the bus.

Source of truth (no HTTP): agents are read from the cofiswarm-agent-registry agent JSON
dir; modes from `ob_code/modes.yaml`. A mode's empty `agents` list resolves to "all
discovered agents". Files are read per query so edits hot-reload without a restart.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml

from bus import subjects as S
from bus.component import ServiceComponent
from bus.contracts.registry import (
    AgentSpec, AgentsReply, ModeSpec, ModesReply, RolesReply, TopologyReply,
)

logger = logging.getLogger(__name__)

DEFAULT_AGENTS_DIR = Path("~/cofiswarm/repos/cofiswarm-agent-registry/data/agents").expanduser()
DEFAULT_MODES_YAML = Path(__file__).resolve().parent.parent / "ob_code" / "modes.yaml"


def load_agents(agents_dir: Path) -> list[AgentSpec]:
    out: list[AgentSpec] = []
    if not agents_dir.exists():
        logger.error("registry: agents dir missing: %s", agents_dir)
        return out
    for path in sorted(agents_dir.glob("*.json")):
        try:
            d = json.loads(path.read_text())
        except Exception:
            logger.error("registry: bad agent json %s", path, exc_info=True)
            continue
        name = d.get("name") or d.get("agent_id")
        if not name:
            continue
        out.append(AgentSpec(
            name=name, server_group=d.get("server_group"),
            system_prompt=d.get("system_prompt", ""),
            port=d.get("port"), tags=d.get("tags", []) or [],
        ))
    return out


def load_modes(modes_yaml: Path) -> dict[str, ModeSpec]:
    try:
        raw = yaml.safe_load(modes_yaml.read_text()) or {}
    except Exception:
        logger.error("registry: cannot read modes yaml %s", modes_yaml, exc_info=True)
        return {}
    out: dict[str, ModeSpec] = {}
    for name, spec in (raw.get("modes") or {}).items():
        spec = spec or {}
        out[name] = ModeSpec(structure=spec.get("structure", ""),
                             description=spec.get("description", ""),
                             agents=spec.get("agents", []) or [])
    return out


def resolve_mode_agents(mode: ModeSpec, all_names: list[str]) -> list[str]:
    """Empty configured list == all discovered agents."""
    return list(mode.agents) if mode.agents else list(all_names)


class RegistryComponent:
    def __init__(self, bus, agents_dir: Path = DEFAULT_AGENTS_DIR,
                 modes_yaml: Path = DEFAULT_MODES_YAML):
        self._agents_dir = Path(agents_dir)
        self._modes_yaml = Path(modes_yaml)
        routes = {
            S.REGISTRY_AGENTS: self._on_agents,
            S.REGISTRY_MODES: self._on_modes,
            S.REGISTRY_ROLES: self._on_roles,
            S.REGISTRY_TOPOLOGY: self._on_topology,
        }
        self._svc = ServiceComponent(bus, name="registry", routes=routes,
                                     kind="registry", component_id="registry", tags=["catalog"])

    async def start(self) -> None:
        await self._svc.start()

    async def shutdown(self) -> None:
        await self._svc.shutdown()

    async def _on_agents(self, data: dict) -> AgentsReply:
        only = set(data.get("only") or [])
        agents = [a for a in load_agents(self._agents_dir) if not only or a.name in only]
        return AgentsReply(agents=agents)

    async def _on_modes(self, data: dict) -> ModesReply:
        only = set(data.get("only") or [])
        names = [a.name for a in load_agents(self._agents_dir)]
        modes = {
            m: ModeSpec(structure=spec.structure, description=spec.description,
                        agents=resolve_mode_agents(spec, names))
            for m, spec in load_modes(self._modes_yaml).items()
            if not only or m in only
        }
        return ModesReply(modes=modes)

    async def _on_roles(self, data: dict) -> RolesReply:
        agents = load_agents(self._agents_dir)
        groups = sorted({a.server_group for a in agents if a.server_group})
        return RolesReply(roles=agents, groups=groups)

    async def _on_topology(self, data: dict) -> TopologyReply:
        names = [a.name for a in load_agents(self._agents_dir)]
        topo = {m: resolve_mode_agents(spec, names)
                for m, spec in load_modes(self._modes_yaml).items()}
        return TopologyReply(modes=topo)
