"""Bridge backend: runs a cofiswarm orchestration MODE via the dispatch service.

cofiswarm-dispatch (:8010) exposes `POST /api/architect/stream` which runs a multi-agent
mode (flat / pipeline / cascade / router) and emits SSE events. This backend POSTs a
prompt for a given mode, parses the SSE taxonomy, and yields a readable token stream with
agent / stage / routing markers — so a whole cofiswarm orchestration appears on the
observer bus as one streamable "model".

Read-only client: it calls dispatch over HTTP and does not modify cofiswarm.
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import aiohttp

logger = logging.getLogger(__name__)


class DispatchModeBackend:
    def __init__(self, mode: str, base_url: str = "http://127.0.0.1:8010", timeout: float = 300.0):
        self._mode = mode
        self._url = base_url.rstrip("/") + "/api/architect/stream"
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self.last_tokens: int | None = None   # summed completion tokens across agents
        self.last_tps: float | None = None    # not reported by dispatch; GUI computes

    async def generate_stream(self, prompt: str, max_tokens: int, history=None,
                              session_id=None, followup=False) -> AsyncIterator[str]:
        self.last_tokens = None
        self.last_tps = None
        body = {"prompt": prompt, "mode": self._mode}
        if session_id:                       # cofiswarm keeps the thread server-side
            body["session_id"] = session_id
            body["followup"] = followup
        current_agent = None
        tok_count = 0  # fallback when the dispatch build omits completion_tokens
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as sess:
                async with sess.post(self._url, json=body) as resp:
                    resp.raise_for_status()
                    event = None
                    async for raw in resp.content:
                        line = raw.decode("utf-8", "replace").rstrip("\r\n")
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            data = line[5:].strip()
                            if event == "done" or data == "[DONE]":
                                if self.last_tokens is None:
                                    self.last_tokens = tok_count or None
                                return
                            if event == "metrics":
                                self._capture_metrics(data)
                                continue
                            if event == "token":
                                tok_count += 1
                            piece, current_agent = _render(event, data, current_agent)
                            if piece:
                                yield piece
        except Exception:
            logger.error("dispatch mode '%s' stream failed", self._mode, exc_info=True)
            raise

    def _capture_metrics(self, data: str) -> None:
        try:
            per_agent = json.loads(data)
            total = sum(int(m.get("completion_tokens", 0)) for m in per_agent.values()
                        if isinstance(m, dict))
            self.last_tokens = total or None
        except Exception:
            logger.error("dispatch mode '%s' bad metrics: %s", self._mode, data, exc_info=True)

    async def generate(self, prompt: str, max_tokens: int, history=None,
                       session_id=None, followup=False) -> str:
        return "".join([p async for p in self.generate_stream(
            prompt, max_tokens, history=history, session_id=session_id, followup=followup)])

    async def close(self) -> None:
        return None


def _render(event: str | None, data: str, current_agent):
    """Map one SSE event to streamed text. Returns (text_or_None, current_agent).

    text == None signals the stream is done.
    """
    if event == "done" or data == "[DONE]":
        return None, current_agent
    try:
        payload = json.loads(data)
    except Exception:
        logger.error("bad SSE data for event %s: %s", event, data, exc_info=True)
        return "", current_agent

    if event == "token":
        agent = payload.get("agent")
        header = ""
        if agent and agent != current_agent:
            header = f"\n\n■ {agent}\n"
            current_agent = agent
        return header + payload.get("delta", ""), current_agent
    if event == "selected":
        return f"\n[router → {', '.join(payload.get('agents', []))}]\n", current_agent
    if event == "stage":
        return f"\n[stage {payload.get('step')}/{payload.get('total')} · {payload.get('agent')}]\n", current_agent
    if event == "synthesis_start":
        return f"\n\n■ synthesis ({payload.get('agent')})\n", current_agent
    return "", current_agent  # session / agent_done / metrics — no visible text
