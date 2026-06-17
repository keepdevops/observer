"""Schema + subject-naming tests for bus.subjects (no broker needed)."""
import json

import pytest
from pydantic import ValidationError

from bus import subjects as S


def test_infer_request_requires_fields():
    with pytest.raises(ValidationError):
        S.InferRequest()  # missing request_id / model / prompt


def test_infer_request_defaults():
    r = S.InferRequest(request_id="x", model="m", prompt="p")
    assert r.stream is False
    assert r.session_id is None
    assert r.max_tokens > 0


def test_model_info_mirrors_cofiswarm_agent():
    info = S.ModelInfo(name="programmer", engine="llama", model="x.gguf",
                       context=1024, max_tokens=2048, port=8086, tags=["coding"])
    assert info.port == 8086
    assert "coding" in info.tags


def test_token_roundtrip_carries_usage():
    t = S.Token(request_id="r", model="m", done=True, tokens=5, tokens_per_sec=10.0)
    d = json.loads(t.model_dump_json())
    assert d["tokens"] == 5 and d["tokens_per_sec"] == 10.0 and d["done"] is True


def test_subject_namespace():
    assert S.tokens_subject("abc") == "swarm.observer.tokens.abc"
    assert S.model_subject("prog") == "swarm.observer.model.prog"
    assert S.PREFIX == "swarm.observer"
