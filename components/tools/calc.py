"""Safe calculator tool: arithmetic only, via a restricted AST (no exec/eval of names/calls).

A deliberately safe demo tool — it proves the Command round-trip without arbitrary code execution.
A real code-running tool would need a sandbox; this one evaluates only numeric expressions.
"""
from __future__ import annotations

import ast
import operator

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("only numeric arithmetic is allowed")


def safe_eval(expr: str):
    return _eval(ast.parse(expr, mode="eval").body)


async def run(args: dict) -> str:
    expr = str(args.get("expr", "")).strip()
    if not expr:
        raise ValueError("calc requires 'expr'")
    return str(safe_eval(expr))
