"""Tool workers: safe calc, ToolWorker round-trip + loud failures (no broker)."""
import asyncio

import pytest

from components.tools import calc
from components.tools.base import ToolWorker


def test_calc_safe_eval_arithmetic():
    assert calc.safe_eval("2 + 3 * 4") == 14
    assert calc.safe_eval("-(2 ** 3)") == -8


def test_calc_rejects_non_arithmetic():
    for bad in ("__import__('os')", "open('x')", "a + 1"):
        with pytest.raises(ValueError):
            calc.safe_eval(bad)


def test_calc_run_requires_expr():
    with pytest.raises(ValueError):
        asyncio.run(calc.run({}))


def test_tool_worker_round_trip():
    worker = ToolWorker(None, "calc", calc.run)
    res = asyncio.run(worker._handle({"tool": "calc", "request_id": "r1", "args": {"expr": "6*7"}}))
    assert res.ok is True and res.output == "42"
    assert res.tool == "calc" and res.request_id == "r1"


def test_tool_worker_reports_run_failure():
    async def boom(_args):
        raise RuntimeError("kaboom")
    res = asyncio.run(ToolWorker(None, "x", boom)._handle({"tool": "x", "args": {}}))
    assert res.ok is False and "kaboom" in res.error


def test_tool_worker_rejects_bad_call():
    res = asyncio.run(ToolWorker(None, "calc", calc.run)._handle({"args": {}}))  # no 'tool'
    assert res.ok is False and "invalid tool call" in res.error
