"""Real inference backend: streams from a llama.cpp / MLX OpenAI-compatible server.

Used to back live cofiswarm agents on the observer bus. Each cofiswarm agent fronts a
local server (its `port` in the agent JSON); this backend POSTs to that server's
`/v1/chat/completions` with stream=true and yields token text as it arrives — turning a
running cofiswarm inference server into a drop-in observer model component.

Read-only client: it does not modify cofiswarm or its servers.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator, Optional

import aiohttp

logger = logging.getLogger(__name__)


class LlamaServerBackend:
    def __init__(self, base_url: str, system_prompt: str, model: str, timeout: float = 120.0,
                 gate: Optional[asyncio.Semaphore] = None):
        self._url = base_url.rstrip("/") + "/v1/chat/completions"
        self._system = system_prompt
        self._model = model
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._gate = gate                          # shared per backing-server, may be None
        self.last_tokens: int | None = None        # real completion tokens (from usage)
        self.last_tps: float | None = None          # server-reported tokens/sec, if any

    async def generate_stream(self, prompt: str, max_tokens: int, history=None,
                              session_id=None, followup=False, system=None) -> AsyncIterator[str]:
        self.last_tokens = None
        self.last_tps = None
        sys_prompt = system if system is not None else self._system  # role×model override
        messages = []
        if sys_prompt:
            messages.append({"role": "system", "content": sys_prompt})
        messages.extend(history or [])           # prior turns for multi-turn context
        messages.append({"role": "user", "content": prompt})
        body = {
            "model": self._model, "messages": messages, "max_tokens": max_tokens,
            "stream": True, "stream_options": {"include_usage": True},
        }
        if self._gate is None:
            async for piece in self._post_stream(body):
                yield piece
        else:
            # Serialize against other agents sharing the same backing server.
            async with self._gate:
                async for piece in self._post_stream(body):
                    yield piece

    async def _post_stream(self, body: dict) -> AsyncIterator[str]:
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as sess:
                async with sess.post(self._url, json=body) as resp:
                    resp.raise_for_status()
                    async for raw in resp.content:
                        line = raw.decode("utf-8", "replace").strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        delta = self._consume(data)
                        if delta:
                            yield delta
        except Exception:
            logger.error("llama backend request failed for %s", self._url, exc_info=True)
            raise

    def _consume(self, data: str) -> str:
        """Parse one SSE chunk: capture usage/timings, return any content delta."""
        try:
            chunk = json.loads(data)
        except Exception:
            logger.error("Bad SSE chunk from %s: %s", self._url, data, exc_info=True)
            return ""
        usage = chunk.get("usage")
        if usage:
            self.last_tokens = usage.get("completion_tokens", self.last_tokens)
        timings = chunk.get("timings")  # llama.cpp extension
        if timings and timings.get("predicted_per_second"):
            self.last_tps = round(timings["predicted_per_second"], 1)
        choices = chunk.get("choices") or []
        if not choices:
            return ""
        return choices[0].get("delta", {}).get("content", "") or ""

    async def generate(self, prompt: str, max_tokens: int, history=None,
                       session_id=None, followup=False) -> str:
        return "".join([p async for p in self.generate_stream(
            prompt, max_tokens, history=history, session_id=session_id, followup=followup)])

    async def close(self) -> None:
        return None
