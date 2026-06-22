"""Lifecycle command builders + loud validation (no subprocess launched)."""
import asyncio

import pytest

from components.lifecycle import LifecycleComponent, build_convert_cmd, build_vllm_cmd

PY = "/usr/bin/python3"


def test_convert_core_and_quantize():
    cmd = build_convert_cmd(PY, {"source": "hf/model", "out": "/m/out", "quantize": "4bit"})
    assert cmd[:4] == [PY, "-m", "mlx_lm.convert", "--hf-path"]
    assert cmd[cmd.index("--mlx-path") + 1] == "/m/out"
    assert "-q" in cmd and cmd[cmd.index("--q-bits") + 1] == "4"


def test_convert_extra_args_last():
    cmd = build_convert_cmd(PY, {"source": "s", "out": "o", "extra_args": ["--x", "1"]})
    assert cmd[-2:] == ["--x", "1"]
    assert "-q" not in cmd  # no quantize requested


def test_convert_requires_source_and_out():
    with pytest.raises(ValueError):
        build_convert_cmd(PY, {"source": "s"})


def test_vllm_core_and_defaults():
    cmd = build_vllm_cmd(PY, {"model": "m"})
    assert "vllm.entrypoints.openai.api_server" in cmd
    assert cmd[cmd.index("--port") + 1] == "8000"
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1"


def test_vllm_requires_model():
    with pytest.raises(ValueError):
        build_vllm_cmd(PY, {"port": 9000})


def test_handler_rejects_bad_input_without_spawning():
    comp = LifecycleComponent(None, python=PY)
    reply = asyncio.run(comp._on_convert({"source": "only-source"}))
    assert reply.ok is False
    assert "out" in reply.error
    assert reply.pid is None
