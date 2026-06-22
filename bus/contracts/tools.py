"""Tool contracts: the Command pattern over the bus (S4).

A `ToolCall` is dispatched to a tool-worker on `.tools.<tool>`; the worker runs the action and
returns a `ToolResult`. The orchestrator invokes tools mid-generation and folds the result back in.
"""
from __future__ import annotations

from typing import Any

from .base import Envelope, ServiceReply


class ToolCall(Envelope):
    tool: str
    request_id: str = ""
    args: dict[str, Any] = {}


class ToolResult(ServiceReply):
    tool: str = ""
    request_id: str = ""
    output: str = ""
