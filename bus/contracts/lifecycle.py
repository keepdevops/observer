"""Model-lifecycle contracts: convert a model, start a vLLM server.

Replaces the legacy `/api/models/convert` and `/api/inference/vllm/start` — the coordinator
capabilities that had no go-forward owner. The lifecycle component launches the job as a
subprocess and replies with a job id + pid; full job tracking/streaming is a follow-on.
"""
from __future__ import annotations

from typing import Optional

from .base import Envelope, ServiceReply


class ConvertRequest(Envelope):
    source: str                       # HF path / model id to convert
    out: str                          # destination path for the converted model
    quantize: Optional[str] = None    # e.g. "4bit" / "8" — omit for no quantization
    extra_args: list[str] = []


class VllmStartRequest(Envelope):
    model: str
    host: str = "127.0.0.1"
    port: int = 8000
    extra_args: list[str] = []


class JobReply(ServiceReply):
    """Result of launching a lifecycle job."""

    job_id: str = ""
    pid: Optional[int] = None
    cmd: list[str] = []
