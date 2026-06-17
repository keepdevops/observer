"""Recorder run-assembly + store tests (handlers called directly, no broker)."""
import asyncio

from recorder.service import Recorder
from recorder.store import HistoryStore


def test_run_assembled_and_recorded(tmp_path):
    store = HistoryStore(tmp_path / "h.jsonl")
    rec = Recorder(bus=None, store=store)

    async def go():
        await rec._on_request(None, {"request_id": "r1", "model": "m", "prompt": "hi"})
        await rec._on_token(None, {"request_id": "r1", "text": "hel"})
        await rec._on_token(None, {"request_id": "r1", "text": "lo"})
        await rec._on_token(None, {"request_id": "r1", "done": True,
                                   "tokens": 2, "tokens_per_sec": 5.0})

    asyncio.run(go())
    rows = store.tail(10)
    assert len(rows) == 1
    r = rows[0]
    assert r["kind"] == "run" and r["status"] == "done"
    assert r["text"] == "hello" and r["tokens"] == 2
    assert "latency_ms" in r


def test_error_run_recorded(tmp_path):
    store = HistoryStore(tmp_path / "h.jsonl")
    rec = Recorder(bus=None, store=store)

    async def go():
        await rec._on_request(None, {"request_id": "r2", "model": "m", "prompt": "x"})
        await rec._on_token(None, {"request_id": "r2", "done": True, "error": "cancelled"})

    asyncio.run(go())
    r = store.tail(10)[0]
    assert r["status"] == "error" and r["error"] == "cancelled"


def test_alert_recorded(tmp_path):
    store = HistoryStore(tmp_path / "h.jsonl")
    rec = Recorder(bus=None, store=store)
    asyncio.run(rec._on_alert(None, {"message": "down", "component_id": "c1"}))
    r = store.tail(10)[0]
    assert r["kind"] == "alert" and r["message"] == "down"


def test_store_skips_malformed_lines(tmp_path):
    p = tmp_path / "h.jsonl"
    p.write_text('{"a":1}\nnot json\n{"b":2}\n')
    assert len(HistoryStore(p).tail(10)) == 2


def test_token_without_request_is_ignored(tmp_path):
    store = HistoryStore(tmp_path / "h.jsonl")
    rec = Recorder(bus=None, store=store)
    asyncio.run(rec._on_token(None, {"request_id": "unknown", "text": "x"}))
    assert store.tail(10) == []
