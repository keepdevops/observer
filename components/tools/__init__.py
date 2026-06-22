"""Tool workers: per-tool standalone processes on `.tools.<tool>` (Command pattern).

Each worker wraps a single async `run(args) -> str` function in a `ToolWorker`. Tools are
polyglot by nature — a Go or C++ worker can join the same subject later; these are the Python
defaults (web fetch, safe calculator).
"""
