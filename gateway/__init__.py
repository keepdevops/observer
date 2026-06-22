"""Observer API gateway: the single HTTP / CLI / WebSocket facade over the bus.

The gateway owns NO business logic. Every external call is translated into a
`swarm.observer.*` request and the reply is relayed back, so HTTP and CLI never drift and
the system has exactly one external surface. It depends on the bus, never on any HTTP
service — that inverted dependency is what makes the bus the real spine. Legacy `/api/*`
routes are added here per sprint (S0: version + swarm status).
"""
