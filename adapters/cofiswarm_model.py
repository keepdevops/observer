"""Model component: a standalone process that joins the bus and serves inference.

Reuses cofiswarm contracts:
  - metadata is loaded from a `cofiswarm-agent-registry` agent JSON when `agent_json`
    is given in the models config;
  - `CofiBackendAdapter` wraps a `cofiswarm-backend-sdk` `InferenceBackend` so a real
    cofiswarm engine (llama.cpp / MLX / vLLM) can serve over the bus.

`EchoBackend` is the zero-dependency fallback so the scaffold runs without cofiswarm
installed and without a real model loaded.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import AsyncIterator, Optional

from bus import subjects as S
from bus.nats_bus import Bus

logger = logging.getLogger(__name__)


class EchoBackend:
    """Trivial backend: echoes the prompt. Lets the scaffold run with no model.

    Streams word-by-word with a small delay so token streaming is observable.
    """

    def __init__(self, model_name: str, delay: float = 0.05):
        self._model = model_name
        self._delay = delay
        self.last_tokens: int | None = None
        self.last_tps: float | None = None

    async def generate_stream(self, prompt: str, max_tokens: int, history=None,
                              session_id=None, followup=False) -> AsyncIterator[str]:
        self.last_tokens = None
        turn = (len(history) // 2 + 1) if history else 1
        text = f"[echo:{self._model} turn {turn}] {prompt.strip()[:200]}"
        words = text.split(" ")
        for word in words:
            if self._delay:
                await asyncio.sleep(self._delay)
            yield word + " "
        self.last_tokens = len(words)  # echo: one "token" per word (approximation)

    async def generate(self, prompt: str, max_tokens: int, history=None,
                       session_id=None, followup=False) -> str:
        parts = [p async for p in self.generate_stream(prompt, max_tokens, history=history)]
        return "".join(parts).rstrip()

    async def close(self) -> None:
        return None


class CofiBackendAdapter:
    """Adapts a cofiswarm-backend-sdk InferenceBackend to the bus's backend surface."""

    def __init__(self, backend):
        self._b = backend

    async def generate_stream(self, prompt: str, max_tokens: int) -> AsyncIterator[str]:
        from cofiswarm_backend.base import GenerateRequest  # lazy: optional dependency

        async for chunk in self._b.generate_stream(
            GenerateRequest(prompt=prompt, max_tokens=max_tokens)
        ):
            if chunk.text:
                yield chunk.text
            if chunk.done:
                break

    async def generate(self, prompt: str, max_tokens: int) -> str:
        parts = [piece async for piece in self.generate_stream(prompt, max_tokens)]
        return "".join(parts)

    async def close(self) -> None:
        await self._b.close()


def load_info(spec: dict) -> S.ModelInfo:
    """Build ModelInfo from a models.yaml entry, optionally merging a cofiswarm agent JSON."""
    data: dict = {}
    agent_path = spec.get("agent_json")
    if agent_path:
        try:
            data = json.loads(Path(agent_path).expanduser().read_text())
        except Exception:
            logger.error("Failed to read cofiswarm agent JSON at %s", agent_path, exc_info=True)
            raise
    merged = {
        "name": spec.get("name") or data.get("name") or data.get("agent_id"),
        "engine": spec.get("engine") or data.get("engine", "echo"),
        "backend": data.get("backend") or data.get("inference_backend"),
        "model": data.get("model"),
        "context": data.get("context", 2048),
        "max_tokens": data.get("max_tokens", 512),
        "server_group": data.get("server_group"),
        "port": data.get("port"),
        "tags": data.get("tags", []),
    }
    return S.ModelInfo(**{k: v for k, v in merged.items() if v is not None})


class ModelComponent:
    """One model, one process. Announces itself, then serves requests over the bus."""

    def __init__(self, bus: Bus, info: S.ModelInfo, backend, component_id: Optional[str] = None):
        self._bus = bus
        self._info = info
        self._backend = backend
        self._cid = component_id or f"model-{info.name}-{uuid.uuid4().hex[:6]}"
        self._subject = S.model_subject(info.name)
        self._active: dict[str, asyncio.Task] = {}  # request_id -> running stream task
        self._history: dict[str, list[dict]] = {}   # session_id -> prior messages
        self._max_turns = 12                         # cap retained turns per session

    async def start(self) -> None:
        await self._bus.subscribe(self._subject, self._on_infer)
        await self._bus.subscribe(S.CANCEL, self._on_cancel)
        await self._bus.subscribe(S.HELLO, self._on_hello)
        await self._announce()
        logger.info("Model '%s' announced on %s (id=%s)", self._info.name, self._subject, self._cid)

    async def _announce(self) -> None:
        await self._bus.publish(
            S.ANNOUNCE,
            S.Announce(component_id=self._cid, info=self._info, infer_subject=self._subject),
        )

    async def _on_hello(self, msg, data: dict) -> None:
        await self._announce()  # middle man (re)started; make ourselves known again

    async def _on_infer(self, msg, data: dict) -> None:
        try:
            req = S.InferRequest(**data)
        except Exception:
            logger.error("Model %s got bad request: %s", self._info.name, data, exc_info=True)
            return
        if req.stream and req.stream_subject:
            self._active[req.request_id] = asyncio.current_task()
            try:
                resp = await self._stream(req)
            finally:
                self._active.pop(req.request_id, None)
        else:
            resp = await self._once(req)
        await msg.respond(resp.model_dump_json().encode())

    async def _on_cancel(self, msg, data: dict) -> None:
        task = self._active.get(data.get("request_id"))
        if task and not task.done():
            task.cancel()
            logger.info("cancel requested for %s on %s", data.get("request_id"), self._info.name)

    def _ctx(self, req: S.InferRequest):
        """Return (history, followup) for this request's session."""
        sid = req.session_id
        if not sid:
            return [], False
        return self._history.get(sid, []), sid in self._history

    def _remember(self, req: S.InferRequest, answer: str) -> None:
        sid = req.session_id
        if not sid or not answer:
            return
        turns = self._history.setdefault(sid, [])
        turns.append({"role": "user", "content": req.prompt})
        turns.append({"role": "assistant", "content": answer})
        del turns[: max(0, len(turns) - self._max_turns * 2)]  # cap retained turns

    async def _once(self, req: S.InferRequest) -> S.InferResponse:
        history, followup = self._ctx(req)
        try:
            text = await self._backend.generate(req.prompt, req.max_tokens,
                                                history=history, session_id=req.session_id,
                                                followup=followup)
            self._remember(req, text)
            return S.InferResponse(request_id=req.request_id, model=self._info.name, text=text)
        except Exception:
            logger.error("Backend generate failed for %s", self._info.name, exc_info=True)
            return S.InferResponse(
                request_id=req.request_id, model=self._info.name, ok=False, error="backend error"
            )

    async def _stream(self, req: S.InferRequest) -> S.InferResponse:
        """Publish Token chunks to req.stream_subject; reply with the full text as ack."""
        seq = 0
        parts: list[str] = []
        history, followup = self._ctx(req)
        try:
            async for piece in self._backend.generate_stream(
                req.prompt, req.max_tokens, history=history,
                session_id=req.session_id, followup=followup,
            ):
                parts.append(piece)
                await self._bus.publish(
                    req.stream_subject,
                    S.Token(request_id=req.request_id, model=self._info.name, seq=seq, text=piece),
                )
                seq += 1
            await self._bus.publish(
                req.stream_subject,
                S.Token(
                    request_id=req.request_id, model=self._info.name, seq=seq, done=True,
                    tokens=getattr(self._backend, "last_tokens", None),
                    tokens_per_sec=getattr(self._backend, "last_tps", None),
                ),
            )
            answer = "".join(parts).rstrip()
            self._remember(req, answer)
            return S.InferResponse(
                request_id=req.request_id, model=self._info.name, text=answer
            )
        except asyncio.CancelledError:
            await self._bus.publish(
                req.stream_subject,
                S.Token(request_id=req.request_id, model=self._info.name, seq=seq,
                        done=True, error="cancelled"),
            )
            logger.info("stream cancelled for %s on %s", req.request_id, self._info.name)
            return S.InferResponse(request_id=req.request_id, model=self._info.name,
                                   ok=False, error="cancelled")
        except Exception:
            logger.error("Streaming failed for %s", self._info.name, exc_info=True)
            await self._bus.publish(
                req.stream_subject,
                S.Token(request_id=req.request_id, model=self._info.name, seq=seq,
                        done=True, error="backend error"),
            )
            return S.InferResponse(
                request_id=req.request_id, model=self._info.name, ok=False, error="backend error"
            )

    async def shutdown(self) -> None:
        try:
            await self._bus.publish(S.GOODBYE, S.Goodbye(component_id=self._cid))
        except Exception:
            logger.error("Failed to publish goodbye for %s", self._cid, exc_info=True)
        await self._backend.close()
