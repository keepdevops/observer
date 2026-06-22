"""ToolWorker: wrap a single async tool function as a bus component (.tools.<tool>)."""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from bus import subjects as S
from bus.component import ServiceComponent
from bus.contracts.tools import ToolCall, ToolResult

logger = logging.getLogger(__name__)

# A tool runs its args dict and returns text output.
RunFn = Callable[[dict], Awaitable[str]]


class ToolWorker:
    def __init__(self, bus, tool: str, run: RunFn):
        self._tool = tool
        self._run = run
        routes = {S.tool_subject(tool): self._handle}
        self._svc = ServiceComponent(bus, name=f"tool-{tool}", routes=routes,
                                     kind="tool", component_id=f"tool-{tool}", tags=["tool"])

    async def start(self) -> None:
        await self._svc.start()

    async def shutdown(self) -> None:
        await self._svc.shutdown()

    async def _handle(self, data: dict) -> ToolResult:
        try:
            call = ToolCall(**data)
        except Exception:
            logger.error("tool %s: bad ToolCall %s", self._tool, data, exc_info=True)
            return ToolResult(ok=False, tool=self._tool, error="invalid tool call")
        try:
            output = await self._run(call.args)
        except Exception as e:
            logger.error("tool %s failed: %s", self._tool, e, exc_info=True)
            return ToolResult(ok=False, tool=self._tool, request_id=call.request_id, error=str(e))
        return ToolResult(tool=self._tool, request_id=call.request_id, output=output)
