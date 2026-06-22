"""MLX model backend: spawn (optional) + serve an `mlx_lm.server` over the bus.

mlx_lm.server is OpenAI-compatible (`/v1/chat/completions`), so streaming reuses
`LlamaServerBackend` verbatim — the MLX-specific work is (1) spawning the server with the
honest **TurboQuant** mapping and (2) the readiness probe.

TurboQuant note (mlx_lm 0.31.3): the server has **no** `--kv-bits`; the KV cache is capped
via `--prompt-cache-bytes`, and quantization is the 4-bit model itself. `build_mlx_args` is a
pure function so the arg shaping is unit-tested without launching anything.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
from pathlib import Path
from typing import AsyncIterator, Optional

import aiohttp

from .llama_backend import LlamaServerBackend

logger = logging.getLogger(__name__)


def mlx_python() -> str:
    """Python that has mlx_lm: $MATRIX_MLX_PYTHON, else the mlx-env, else PATH python3."""
    env = os.environ.get("MATRIX_MLX_PYTHON")
    if env:
        return env
    cand = Path.home() / "miniforge3/envs/mlx-env/bin/python"
    return str(cand) if cand.exists() else "python3"


def _resolve_model(spec: dict) -> str:
    model = spec.get("model") or os.environ.get("MATRIX_MLX_MODEL")
    if not model:
        raise ValueError("mlx model requires a 'model' path (or $MATRIX_MLX_MODEL)")
    return os.path.expanduser(str(model))


def build_mlx_args(spec: dict) -> list[str]:
    """Build `mlx_lm.server` CLI args (without the python prefix). Extra args go last."""
    if not spec.get("port"):
        raise ValueError("mlx model requires a 'port'")
    args = ["-m", "mlx_lm.server", "--model", _resolve_model(spec),
            "--host", str(spec.get("host", "127.0.0.1")), "--port", str(spec["port"])]
    if spec.get("max_tokens"):
        args += ["--max-tokens", str(spec["max_tokens"])]
    if spec.get("prompt_cache_bytes"):                       # TurboQuant KV cap
        args += ["--prompt-cache-bytes", str(spec["prompt_cache_bytes"])]
    if spec.get("draft_model"):                              # speculative decoding
        args += ["--draft-model", os.path.expanduser(str(spec["draft_model"]))]
        if spec.get("num_draft_tokens"):
            args += ["--num-draft-tokens", str(spec["num_draft_tokens"])]
    if spec.get("trust_remote_code"):
        args += ["--trust-remote-code"]
    args += [str(a) for a in spec.get("extra_args", [])]     # ExtraArgs last
    return args


class MLXServer:
    """Manages one `mlx_lm.server` process (spawn / readiness / stop)."""

    def __init__(self, spec: dict, log_dir: Path = Path(".run")):
        self._spec = spec
        self._host = spec.get("host", "127.0.0.1")
        self._port = int(spec["port"])
        self._log_dir = Path(log_dir)
        self._proc: Optional[asyncio.subprocess.Process] = None

    def _port_open(self) -> bool:
        s = socket.socket()
        s.settimeout(0.3)
        try:
            return s.connect_ex((self._host, self._port)) == 0
        finally:
            s.close()

    async def spawn(self) -> None:
        if self._port_open():
            logger.info("MLX already serving on %s:%s — connecting (no spawn)",
                        self._host, self._port)
            return
        cmd = [mlx_python()] + build_mlx_args(self._spec)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        logf = open(self._log_dir / f"mlx-{self._port}.log", "ab")  # noqa: SIM115 (child stdio)
        logger.info("spawning MLX: %s", " ".join(cmd))
        self._proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=logf, stderr=logf, start_new_session=True)

    async def await_ready(self, timeout: float = 120.0) -> bool:
        url = f"http://{self._host}:{self._port}/v1/models"
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        async with aiohttp.ClientSession() as sess:
            while loop.time() < deadline:
                try:
                    async with sess.get(url, timeout=aiohttp.ClientTimeout(total=2)) as r:
                        if r.status == 200:
                            return True
                except Exception:
                    pass  # not up yet — keep polling until the deadline
                await asyncio.sleep(1.0)
        logger.error("MLX server on :%s not ready within %ss", self._port, timeout)
        return False

    async def stop(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                logger.error("MLX server on :%s did not exit; killing", self._port)
                self._proc.kill()


class MLXServerBackend:
    """Bus backend for an MLX model: optional spawn + OpenAI-compatible streaming client."""

    def __init__(self, spec: dict, manage: bool = True):
        self._manage = manage
        self._server = MLXServer(spec) if manage else None
        host, port = spec.get("host", "127.0.0.1"), spec["port"]
        self._client = LlamaServerBackend(
            base_url=f"http://{host}:{port}",
            system_prompt=spec.get("system_prompt", ""),
            model=spec.get("model") or spec.get("name") or "mlx",
        )

    @property
    def last_tokens(self):
        return self._client.last_tokens

    @property
    def last_tps(self):
        return self._client.last_tps

    async def ensure_ready(self, timeout: float = 120.0) -> bool:
        if not self._manage or self._server is None:
            return True
        await self._server.spawn()
        return await self._server.await_ready(timeout)

    async def generate_stream(self, prompt: str, max_tokens: int, history=None,
                              session_id=None, followup=False, system=None) -> AsyncIterator[str]:
        async for piece in self._client.generate_stream(
            prompt, max_tokens, history=history, session_id=session_id,
            followup=followup, system=system,
        ):
            yield piece

    async def generate(self, prompt: str, max_tokens: int, history=None,
                       session_id=None, followup=False, system=None) -> str:
        return "".join([p async for p in self.generate_stream(
            prompt, max_tokens, history=history, session_id=session_id,
            followup=followup, system=system)])

    async def close(self) -> None:
        await self._client.close()
        if self._server is not None:
            await self._server.stop()
