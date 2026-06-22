"""Native orchestrator backend: multi-agent fan-out over the bus (no HTTP).

Replaces `dispatch_backend.DispatchModeBackend`. Implements the same backend surface
(`generate_stream` / `generate` / `close`) so a whole orchestration MODE appears on the bus
as one streamable "model" (`swarm-<mode>`), exactly like before — but instead of POSTing to
cofiswarm-dispatch (:8010) it calls the agent model components directly over the bus and
streams their tokens back with agent / stage markers.

Modes (agents resolved from the registry component):
  - flat:     every agent answers the same prompt (sequential, each block marked).
  - pipeline: agents run in order; each stage's output feeds the next.
  - cascade:  broadcast like flat, then the last agent synthesizes over the joined outputs.
  - router:   send only to the configured subset (a real classifier is a follow-on).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import uuid
from typing import AsyncIterator, Optional

from bus import subjects as S
from bus.contracts.registry import ModesQuery
from bus.contracts.tools import ToolCall

logger = logging.getLogger(__name__)

AGENT_TIMEOUT = 300.0
REGISTRY_TIMEOUT = 3.0
TOOL_TIMEOUT = 60.0
# Agents request a tool by emitting `[[tool:NAME {json-args}]]` in their output (Command pattern).
TOOL_RE = re.compile(r"\[\[tool:(\w+)\s+(\{.*?\})\]\]", re.DOTALL)


class Orchestrator:
    """One mode, wrapped as a bus backend. Fans out to agents over the bus."""

    def __init__(self, bus, mode: str, agent_timeout: float = AGENT_TIMEOUT):
        self._bus = bus
        self._mode = mode
        self._timeout = agent_timeout
        self.last_tokens: Optional[int] = None
        self.last_tps: Optional[float] = None

    async def _agents(self) -> list[str]:
        """Resolve this mode's agent list from the registry component (bus request)."""
        try:
            reply = await self._bus.request(
                S.REGISTRY_MODES, ModesQuery(only=[self._mode]), timeout=REGISTRY_TIMEOUT)
        except Exception:
            logger.error("orchestrator '%s': registry unreachable", self._mode, exc_info=True)
            raise
        spec = (reply.get("modes") or {}).get(self._mode)
        if not spec or not spec.get("agents"):
            raise RuntimeError(f"no agents registered for mode '{self._mode}'")
        return list(spec["agents"])

    async def _stream_agent(self, agent: str, prompt: str, max_tokens: int,
                            system: Optional[str] = None) -> AsyncIterator[str]:
        """Request one agent with streaming; yield its token texts as they arrive.

        Calls the agent's model subject directly (intra-swarm), subscribing to a private
        token subject first so no chunk is missed.
        """
        rid = uuid.uuid4().hex
        queue: asyncio.Queue[dict] = asyncio.Queue()

        async def _enqueue(_msg, data: dict) -> None:
            await queue.put(data)

        sub = await self._bus.subscribe(S.tokens_subject(rid), _enqueue)
        req = S.InferRequest(request_id=rid, model=agent, prompt=prompt, max_tokens=max_tokens,
                             stream=True, stream_subject=S.tokens_subject(rid), system=system)
        ack = asyncio.create_task(
            self._bus.request(S.model_subject(agent), req, timeout=self._timeout))
        try:
            while True:
                tok = await asyncio.wait_for(queue.get(), timeout=self._timeout)
                if tok.get("error"):
                    raise RuntimeError(f"{agent}: {tok['error']}")
                if tok.get("done"):
                    if tok.get("tokens"):
                        self.last_tokens = (self.last_tokens or 0) + int(tok["tokens"])
                    break
                text = tok.get("text", "")
                if text:
                    yield text
        finally:
            with contextlib.suppress(Exception):
                await sub.unsubscribe()
            with contextlib.suppress(Exception):
                await ack

    async def generate_stream(self, prompt: str, max_tokens: int, history=None,
                              session_id=None, followup=False, system=None) -> AsyncIterator[str]:
        self.last_tokens = None
        self.last_tps = None
        agents = await self._agents()
        if self._mode == "pipeline":
            async for piece in self._run_pipeline(agents, prompt, max_tokens):
                yield piece
        elif self._mode == "cascade":
            async for piece in self._run_cascade(agents, prompt, max_tokens):
                yield piece
        else:  # flat and router both broadcast to their (already-resolved) agent list
            async for piece in self._run_flat(agents, prompt, max_tokens):
                yield piece

    async def _run_flat(self, agents, prompt, max_tokens) -> AsyncIterator[str]:
        for agent in agents:
            yield f"\n\n■ {agent}\n"
            parts: list[str] = []
            async for piece in self._stream_agent(agent, prompt, max_tokens):
                parts.append(piece)
                yield piece
            async for extra in self._resolve_tools("".join(parts)):
                yield extra

    async def _run_pipeline(self, agents, prompt, max_tokens) -> AsyncIterator[str]:
        current = prompt
        total = len(agents)
        for step, agent in enumerate(agents, start=1):
            yield f"\n[stage {step}/{total} · {agent}]\n"
            parts: list[str] = []
            async for piece in self._stream_agent(agent, current, max_tokens):
                parts.append(piece)
                yield piece
            # Command pattern: run any tool calls this stage emitted and fold results in, so the
            # next stage resumes with the tool-augmented output.
            text, tool_out = "".join(parts), []
            async for extra in self._resolve_tools(text):
                tool_out.append(extra)
                yield extra
            current = (text + "".join(tool_out)).strip() or current

    async def _resolve_tools(self, text: str) -> AsyncIterator[str]:
        """Detect `[[tool:NAME {args}]]` markers, invoke each over the bus, yield the results."""
        for match in TOOL_RE.finditer(text):
            tool, raw = match.group(1), match.group(2)
            try:
                args = json.loads(raw)
            except Exception:
                logger.error("orchestrator: bad tool args for %s: %s", tool, raw)
                yield f"\n[[tool:{tool} error]] bad args\n"
                continue
            output = await self._call_tool(tool, args)
            yield f"\n[[tool:{tool} result]] {output}\n"

    async def _call_tool(self, tool: str, args: dict) -> str:
        """Dispatch one ToolCall over the bus and return its output (loud on failure)."""
        rid = uuid.uuid4().hex
        try:
            reply = await self._bus.request(
                S.tool_subject(tool), ToolCall(tool=tool, request_id=rid, args=args),
                timeout=TOOL_TIMEOUT)
        except Exception:
            logger.error("orchestrator: tool '%s' unavailable", tool, exc_info=True)
            return "(tool unavailable)"
        if not reply.get("ok", True):
            return f"(tool error: {reply.get('error')})"
        return reply.get("output", "")

    async def _run_cascade(self, agents, prompt, max_tokens) -> AsyncIterator[str]:
        if len(agents) < 2:
            async for piece in self._run_flat(agents, prompt, max_tokens):
                yield piece
            return
        workers, synth = agents[:-1], agents[-1]
        outputs: list[str] = []
        for agent in workers:
            yield f"\n\n■ {agent}\n"
            parts: list[str] = []
            async for piece in self._stream_agent(agent, prompt, max_tokens):
                parts.append(piece)
                yield piece
            outputs.append(f"## {agent}\n{''.join(parts).strip()}")
        joined = "\n\n".join(outputs)
        synth_prompt = (f"Synthesize one answer to:\n{prompt}\n\n"
                        f"From these agent answers:\n{joined}")
        yield f"\n\n■ synthesis ({synth})\n"
        async for piece in self._stream_agent(synth, synth_prompt, max_tokens):
            yield piece

    async def generate(self, prompt: str, max_tokens: int, history=None,
                       session_id=None, followup=False, system=None) -> str:
        return "".join([p async for p in self.generate_stream(prompt, max_tokens)])

    async def close(self) -> None:
        return None
