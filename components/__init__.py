"""Standalone capability components (request/reply over the bus).

Each module here is one coordinator capability as its own process, built on
`bus.component.ServiceComponent`. Mechanical naming: capability `foo` -> `.foo.*` subjects
(bus/subjects.py) -> `bus/contracts/foo.py` envelopes -> `components/foo.py` -> `run_foo.py`.
"""
