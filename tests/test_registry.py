"""Registry component: loading + per-subject replies (no broker)."""
import json

import pytest

from bus.contracts.registry import ModeSpec
from components.registry import (
    RegistryComponent, load_agents, load_modes, resolve_mode_agents,
)


@pytest.fixture
def agents_dir(tmp_path):
    (tmp_path / "programmer.json").write_text(json.dumps(
        {"name": "programmer", "server_group": "g1", "system_prompt": "code.",
         "port": 8086, "tags": ["coding"]}))
    (tmp_path / "reviewer.json").write_text(json.dumps(
        {"agent_id": "reviewer", "server_group": "g2"}))
    (tmp_path / "broken.json").write_text("{not json")  # tolerated, logged
    return tmp_path


@pytest.fixture
def modes_yaml(tmp_path):
    p = tmp_path / "modes.yaml"
    p.write_text(
        "modes:\n"
        "  flat: {structure: fan-out, description: all, agents: []}\n"
        "  router: {structure: routed, description: subset, agents: [programmer]}\n")
    return p


def test_load_agents_skips_bad_and_nameless(agents_dir):
    names = sorted(a.name for a in load_agents(agents_dir))
    assert names == ["programmer", "reviewer"]


def test_resolve_mode_agents_empty_means_all():
    assert resolve_mode_agents(ModeSpec(agents=[]), ["a", "b"]) == ["a", "b"]
    assert resolve_mode_agents(ModeSpec(agents=["a"]), ["a", "b"]) == ["a"]


def test_modes_reply_resolves_and_filters(agents_dir, modes_yaml):
    comp = RegistryComponent(None, agents_dir=agents_dir, modes_yaml=modes_yaml)
    reply = _run(comp._on_modes({"only": ["flat"]}))
    assert set(reply.modes) == {"flat"}
    assert sorted(reply.modes["flat"].agents) == ["programmer", "reviewer"]  # empty -> all


def test_modes_reply_keeps_curated_subset(agents_dir, modes_yaml):
    comp = RegistryComponent(None, agents_dir=agents_dir, modes_yaml=modes_yaml)
    reply = _run(comp._on_modes({"only": ["router"]}))
    assert reply.modes["router"].agents == ["programmer"]


def test_roles_lists_distinct_groups(agents_dir, modes_yaml):
    comp = RegistryComponent(None, agents_dir=agents_dir, modes_yaml=modes_yaml)
    reply = _run(comp._on_roles({}))
    assert reply.groups == ["g1", "g2"]
    assert {r.name for r in reply.roles} == {"programmer", "reviewer"}


def test_load_modes_missing_file_is_empty(tmp_path):
    assert load_modes(tmp_path / "nope.yaml") == {}


def _run(coro):
    import asyncio
    return asyncio.run(coro)
