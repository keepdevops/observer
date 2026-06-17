"""observer bus package: NATS broker transport + event-driven presence + middle man.

Import submodules directly, e.g. `from bus.nats_bus import Bus`. Nothing is imported
eagerly here so the broker-free core (`bus.subjects`, `bus.presence`) stays usable and
testable without the `nats` dependency installed.
"""
