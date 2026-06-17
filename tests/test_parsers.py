"""SSE / usage parsing tests for the dispatch + llama backends (no network)."""
import json

from adapters.dispatch_backend import DispatchModeBackend, _render
from adapters.llama_backend import LlamaServerBackend


# ---- dispatch mode SSE rendering ----

def test_render_token_inserts_agent_header():
    piece, agent = _render("token", json.dumps({"agent": "architect", "delta": "Hi"}), None)
    assert "architect" in piece and piece.endswith("Hi") and agent == "architect"


def test_render_token_same_agent_no_repeat_header():
    piece, agent = _render("token", json.dumps({"agent": "a", "delta": "x"}), "a")
    assert piece == "x" and agent == "a"


def test_render_done_signals_end():
    piece, _ = _render("done", "[DONE]", None)
    assert piece is None


def test_render_router_selected():
    piece, _ = _render("selected", json.dumps({"agents": ["a", "b"]}), None)
    assert "a, b" in piece


def test_capture_metrics_sums_completion_tokens():
    b = DispatchModeBackend("flat")
    b._capture_metrics(json.dumps({"a": {"completion_tokens": 3}, "b": {"completion_tokens": 4}}))
    assert b.last_tokens == 7


def test_capture_metrics_minimal_payload_is_none():
    b = DispatchModeBackend("flat")
    b._capture_metrics(json.dumps({"calls": 1, "stream": True}))
    assert b.last_tokens is None


# ---- llama OpenAI-stream chunk parsing ----

def test_llama_consume_delta():
    b = LlamaServerBackend("http://x", "sys", "m")
    assert b._consume(json.dumps({"choices": [{"delta": {"content": "Hi"}}]})) == "Hi"


def test_llama_consume_usage_chunk():
    b = LlamaServerBackend("http://x", "sys", "m")
    assert b._consume(json.dumps({"choices": [], "usage": {"completion_tokens": 12}})) == ""
    assert b.last_tokens == 12


def test_llama_consume_timings_tps():
    b = LlamaServerBackend("http://x", "sys", "m")
    b._consume(json.dumps({"choices": [], "timings": {"predicted_per_second": 42.4}}))
    assert b.last_tps == 42.4
