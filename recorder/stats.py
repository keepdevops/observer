"""Aggregate recorded runs into per-model stats. Pure function — easy to test."""
from __future__ import annotations

from collections import defaultdict


def _avg(xs: list[float]):
    return round(sum(xs) / len(xs), 1) if xs else None


def aggregate(records: list[dict]) -> list[dict]:
    """Group run records by model -> {runs, errors, avg latency / tokens / tok-per-sec}."""
    groups: dict[str, dict] = defaultdict(lambda: {"runs": 0, "errors": 0, "lat": [], "tok": [], "tps": []})
    for rec in records:
        if rec.get("kind") != "run":
            continue
        g = groups[rec.get("model") or "?"]
        g["runs"] += 1
        if rec.get("status") != "done":
            g["errors"] += 1
        for key, field in (("lat", "latency_ms"), ("tok", "tokens"), ("tps", "tokens_per_sec")):
            val = rec.get(field)
            if isinstance(val, (int, float)):
                g[key].append(val)

    out = [
        {
            "model": model, "runs": g["runs"], "errors": g["errors"],
            "avg_latency_ms": _avg(g["lat"]), "avg_tokens": _avg(g["tok"]), "avg_tps": _avg(g["tps"]),
        }
        for model, g in groups.items()
    ]
    out.sort(key=lambda x: -x["runs"])
    return out
