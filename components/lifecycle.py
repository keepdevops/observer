"""Lifecycle component: model convert + vLLM start, served over the bus.

Pure command builders (`build_convert_cmd` / `build_vllm_cmd`) are unit-tested without
launching anything; the component spawns the built command as a detached subprocess and
replies with a job id + pid. Bad input is surfaced loudly as `ok=False` (not a generic
handler error), per the no-silent-failures rule.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from adapters.mlx_backend import mlx_python
from bus import subjects as S
from bus.component import ServiceComponent
from bus.contracts.lifecycle import JobReply

logger = logging.getLogger(__name__)


def build_convert_cmd(python: str, data: dict) -> list[str]:
    """`mlx_lm.convert --hf-path <source> --mlx-path <out> [-q --q-bits N] [extra...]`."""
    source, out = data.get("source"), data.get("out")
    if not source or not out:
        raise ValueError("convert requires 'source' and 'out'")
    cmd = [python, "-m", "mlx_lm.convert", "--hf-path", str(source), "--mlx-path", str(out)]
    q = data.get("quantize")
    if q:
        cmd.append("-q")
        bits = str(q).replace("bit", "").strip()
        if bits.isdigit():
            cmd += ["--q-bits", bits]
    cmd += [str(a) for a in data.get("extra_args", [])]
    return cmd


def build_vllm_cmd(python: str, data: dict) -> list[str]:
    """`vllm.entrypoints.openai.api_server --model <m> --host <h> --port <p> [extra...]`."""
    model = data.get("model")
    if not model:
        raise ValueError("vllm start requires 'model'")
    cmd = [python, "-m", "vllm.entrypoints.openai.api_server", "--model", str(model),
           "--host", str(data.get("host", "127.0.0.1")), "--port", str(data.get("port", 8000))]
    cmd += [str(a) for a in data.get("extra_args", [])]
    return cmd


class LifecycleComponent:
    def __init__(self, bus, python: str | None = None, log_dir: Path = Path(".run")):
        self._python = python or mlx_python()
        self._log_dir = Path(log_dir)
        routes = {
            S.LIFECYCLE_CONVERT: self._on_convert,
            S.LIFECYCLE_VLLM_START: self._on_vllm,
        }
        self._svc = ServiceComponent(bus, name="lifecycle", routes=routes,
                                     kind="lifecycle", component_id="lifecycle", tags=["models"])

    async def start(self) -> None:
        await self._svc.start()

    async def shutdown(self) -> None:
        await self._svc.shutdown()

    async def _spawn(self, cmd: list[str], tag: str) -> JobReply:
        job_id = uuid.uuid4().hex[:8]
        self._log_dir.mkdir(parents=True, exist_ok=True)
        logf = open(self._log_dir / f"{tag}-{job_id}.log", "ab")  # noqa: SIM115 (child stdio)
        logger.info("lifecycle %s job %s: %s", tag, job_id, " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=logf, stderr=logf, start_new_session=True)
        return JobReply(job_id=job_id, pid=proc.pid, cmd=cmd)

    async def _on_convert(self, data: dict) -> JobReply:
        try:
            cmd = build_convert_cmd(self._python, data)
        except ValueError as e:
            logger.error("convert rejected: %s", e)
            return JobReply(ok=False, error=str(e))
        return await self._spawn(cmd, "convert")

    async def _on_vllm(self, data: dict) -> JobReply:
        try:
            cmd = build_vllm_cmd(self._python, data)
        except ValueError as e:
            logger.error("vllm start rejected: %s", e)
            return JobReply(ok=False, error=str(e))
        return await self._spawn(cmd, "vllm")
