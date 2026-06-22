"""Resource contracts: field shapes mirror the Go services' JSON tags (no broker)."""
import json

from bus import subjects as S
from bus.contracts.resource import (
    ConfigureRequest, EvictRequest, KvAdmitReply, KvEvaluateReply, KvPolicyReply,
    LaunchAgent, PressureReading, PressureReply,
)


def test_kv_admit_reply_fields_match_go():
    d = json.loads(KvAdmitReply(allowed=False, budget=100, reason="token_budget_exceeded")
                   .model_dump_json())
    assert {"allowed", "budget", "reason", "ok", "schema_version"} <= set(d)


def test_kv_evaluate_reply_fields_match_go():
    d = json.loads(KvEvaluateReply(auto_clear=True, reason="pressure_threshold").model_dump_json())
    assert d["auto_clear"] is True and d["proactive_evict"] is False
    assert "proactive_fraction" in d


def test_kv_policy_reply_carries_budgets():
    r = KvPolicyReply(enabled=True, pressure_threshold=0.75, budgets={"llama8b": 4096})
    assert json.loads(r.model_dump_json())["budgets"] == {"llama8b": 4096}


def test_pressure_reply_nests_readings():
    r = PressureReply(readings=[PressureReading(endpoint_id="e1", port=8086, usage=0.9)])
    assert r.readings[0].usage == 0.9


def test_evict_defaults_to_all_endpoints():
    assert EvictRequest().endpoint_id == ""


def test_configure_carries_agents_with_tuning():
    c = ConfigureRequest(agents=[
        LaunchAgent(name="programmer", model="m.gguf", port=8086,
                    backend="mlx", rope_scaling="yarn", kv_cache_type="q4_0", turbo_quant=True),
    ])
    a = c.agents[0]
    assert a.backend == "mlx" and a.rope_scaling == "yarn"
    assert a.kv_cache_type == "q4_0" and a.turbo_quant is True


def test_launch_agent_field_names_match_go():
    import json
    d = json.loads(LaunchAgent(name="a", model="m", port=1).model_dump_json())
    assert {"name", "model", "port", "backend", "kv_cache_type", "rope_scaling",
            "turbo_quant", "extra_args"} <= set(d)


def test_subjects_renamed_to_match_kvpool_api():
    assert S.KV_EVALUATE.endswith(".kv.evaluate")
    assert not hasattr(S, "KV_BUDGET")
