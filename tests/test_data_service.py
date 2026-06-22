"""Data service Repository handlers (no broker)."""
import asyncio

from bus import subjects as S
from components.data_service import DataService, KVRepo


def _run(coro):
    return asyncio.run(coro)


def test_kv_put_get_list_delete():
    kv = KVRepo()
    assert kv.handle({"op": "get", "key": "x"}).value is None
    assert kv.handle({"op": "put", "key": "x", "value": 42}).value == 42
    assert kv.handle({"op": "get", "key": "x"}).value == 42
    assert kv.handle({"op": "list"}).items == ["x"]
    kv.handle({"op": "delete", "key": "x"})
    assert kv.handle({"op": "get", "key": "x"}).value is None


def test_kv_unsupported_op_fails_loud():
    r = KVRepo().handle({"op": "frobnicate"})
    assert r.ok is False and "frobnicate" in r.error


def test_memory_route_sets_resource(tmp_path):
    svc = DataService(None)
    route = svc._wrap("memory", svc._stores["memory"].handle)
    r = _run(route({"op": "put", "key": "k", "value": "v"}))
    assert r.resource == "memory" and r.value == "v"


def test_models_handler_reads_yaml(tmp_path):
    p = tmp_path / "models.yaml"
    p.write_text("models:\n  - name: echo-fast\n  - name: mlx-scout\n")
    svc = DataService(None, models_yaml=p)
    r = svc._models_handler({})
    assert r.count == 2 and r.items[0]["name"] == "echo-fast"


def test_logs_handler_tails_run_dir(tmp_path):
    (tmp_path / "gui.log").write_text("a\nb\nc\nd\n")
    svc = DataService(None, run_dir=tmp_path)
    r = svc._logs_handler({"key": "gui", "params": {"limit": 2}})
    assert r.items == ["c", "d"]


def test_logs_missing_fails_loud(tmp_path):
    svc = DataService(None, run_dir=tmp_path)
    assert svc._logs_handler({"key": "nope"}).ok is False
    assert svc._logs_handler({}).ok is False  # missing key


def test_rag_health_reports_status():
    r = DataService(None)._rag_handler({})
    assert r.ok is True and r.value["status"] in ("configured", "no-rag-configured")


def test_data_subjects_cover_resources():
    assert S.data_subject("history") == "swarm.observer.data.history"
