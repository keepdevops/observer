"""Per-model aggregation tests for recorder.stats.aggregate."""
from recorder.stats import aggregate


def test_aggregate_groups_and_averages():
    records = [
        {"kind": "run", "model": "m1", "status": "done", "latency_ms": 100, "tokens": 10, "tokens_per_sec": 50.0},
        {"kind": "run", "model": "m1", "status": "done", "latency_ms": 300, "tokens": 30, "tokens_per_sec": 70.0},
        {"kind": "run", "model": "m1", "status": "error", "latency_ms": 50},
        {"kind": "run", "model": "m2", "status": "done", "latency_ms": 200, "tokens": 20},
        {"kind": "alert", "message": "ignored"},
    ]
    out = {r["model"]: r for r in aggregate(records)}
    assert out["m1"]["runs"] == 3 and out["m1"]["errors"] == 1
    assert out["m1"]["avg_latency_ms"] == 150.0      # (100+300+50)/3
    assert out["m1"]["avg_tokens"] == 20.0           # (10+30)/2, error run has none
    assert out["m1"]["avg_tps"] == 60.0              # (50+70)/2
    assert out["m2"]["runs"] == 1 and out["m2"]["avg_tps"] is None


def test_aggregate_sorted_by_runs_desc():
    records = [
        {"kind": "run", "model": "a", "status": "done"},
        {"kind": "run", "model": "b", "status": "done"},
        {"kind": "run", "model": "b", "status": "done"},
    ]
    assert [r["model"] for r in aggregate(records)] == ["b", "a"]


def test_aggregate_empty():
    assert aggregate([]) == []
