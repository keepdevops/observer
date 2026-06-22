"""MLX arg shaping: TurboQuant KV cap, draft model, extra-args ordering (no spawn)."""
import pytest

from adapters.mlx_backend import build_mlx_args


def _base(**extra):
    return {"name": "mlx-scout", "model": "/m/scout", "port": 8083, **extra}


def test_core_args_present():
    args = build_mlx_args(_base())
    assert args[:2] == ["-m", "mlx_lm.server"]
    assert "--model" in args and "/m/scout" in args
    assert args[args.index("--port") + 1] == "8083"


def test_turboquant_prompt_cache_cap():
    args = build_mlx_args(_base(prompt_cache_bytes=536870912))
    assert args[args.index("--prompt-cache-bytes") + 1] == "536870912"
    assert "--kv-bits" not in args  # mlx_lm.server has none; KV is capped via prompt-cache


def test_draft_model_adds_speculative_flags():
    args = build_mlx_args(_base(draft_model="/m/draft", num_draft_tokens=4))
    assert args[args.index("--draft-model") + 1] == "/m/draft"
    assert args[args.index("--num-draft-tokens") + 1] == "4"


def test_extra_args_go_last():
    args = build_mlx_args(_base(extra_args=["--foo", "bar"]))
    assert args[-2:] == ["--foo", "bar"]


def test_trust_remote_code_flag():
    assert "--trust-remote-code" in build_mlx_args(_base(trust_remote_code=True))
    assert "--trust-remote-code" not in build_mlx_args(_base())


def test_model_from_env(monkeypatch):
    monkeypatch.setenv("MATRIX_MLX_MODEL", "/env/model")
    args = build_mlx_args({"name": "x", "port": 8083})  # no model in spec
    assert "/env/model" in args


def test_missing_model_and_port_raise(monkeypatch):
    monkeypatch.delenv("MATRIX_MLX_MODEL", raising=False)
    with pytest.raises(ValueError):
        build_mlx_args({"name": "x", "port": 8083})       # no model
    with pytest.raises(ValueError):
        build_mlx_args({"name": "x", "model": "/m"})      # no port
