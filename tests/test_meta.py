"""Observability component: metrics / health / config (no broker)."""
import asyncio
import json

from components.observability import Observability, agents_from_roster


def _run(coro):
    return asyncio.run(coro)


def test_agents_from_roster_maps_and_sorts():
    rows = agents_from_roster([
        {"component_id": "c2", "status": "online", "info": {"name": "zed", "engine": "llama"}},
        {"component_id": "c1", "status": "online", "info": {"name": "abe", "engine": "echo"}},
    ])
    assert [r["name"] for r in rows] == ["abe", "zed"]
    assert rows[0]["engine"] == "echo"


def test_metrics_aggregates_history(tmp_path, monkeypatch):
    obs = Observability(None)
    # feed a fake store
    obs._store.tail = lambda n=1000: [
        {"kind": "run", "model": "m", "status": "done", "latency_ms": 100, "tokens": 10},
        {"kind": "run", "model": "m", "status": "error"},
    ]
    r = _run(obs._on_metrics({}))
    assert r.total_runs == 2
    assert r.models[0]["model"] == "m" and r.models[0]["errors"] == 1


def test_config_get_empty_then_set_then_get(tmp_path):
    obs = Observability(None, config_path=tmp_path / "cfg.json")
    assert _run(obs._on_config({"op": "get"})).config == {}
    r = _run(obs._on_config({"op": "set", "key": "theme", "value": "dark"}))
    assert r.config["theme"] == "dark"
    assert _run(obs._on_config({"op": "get"})).config == {"theme": "dark"}


def test_config_set_requires_key(tmp_path):
    obs = Observability(None, config_path=tmp_path / "cfg.json")
    r = _run(obs._on_config({"op": "set"}))
    assert r.ok is False and "key" in r.error


def test_config_unsupported_op(tmp_path):
    obs = Observability(None, config_path=tmp_path / "cfg.json")
    assert _run(obs._on_config({"op": "frob"})).ok is False


def test_config_get_tolerates_bad_file(tmp_path):
    p = tmp_path / "cfg.json"
    p.write_text("{not json")
    obs = Observability(None, config_path=p)
    assert _run(obs._on_config({"op": "get"})).config == {}
