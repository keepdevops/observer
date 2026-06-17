# Migration plan — `cofiswarm-zmq-bridge` → the diagram's "middle man"

> Companion to [`../diagram.md`](../diagram.md). This is the keystone change for making the
> cofiswarm monorepo adopt the observer architecture (single NATS broker + event-driven,
> no-heartbeat presence). `~/observer` is the working reference implementation of these semantics.

## Goal
Back the bridge's bus with a **real NATS broker** (actual pub/sub fan-out) while keeping the existing
HTTP control plane 100% backward-compatible. Additive and flag-gated — nothing else in cofiswarm has
to change to merge it.

## Current state (as read, 2026-06)
- `internal/bus/bus.go` — `Bus{topics, events}`; `Publish` appends to a 256-entry ring; `Recent()`
  returns it. No subscribers, no ZMQ. ("real ZMQ in Sprint 13+".)
- `internal/httpapi/server.go` — `/healthz`, `/v1/topics`, `/v1/publish` (POST), `/v1/events` (GET).
- `cmd/cofiswarm-zmq-bridge/main.go` — load `topics.yaml`, `bus.New`, serve HTTP.
- `go.mod` — Go 1.22, only `gopkg.in/yaml.v3`.

## Target shape
Turn the concrete `Bus` into an interface with two backends; add a real subscribe path and an
HTTP→bus bridge so non-NATS clients (the observer, browsers) can consume the bus.

```
internal/bus/
  backend.go   // type Backend interface { Publish; Subscribe; Topics }
  mem.go       // current in-memory impl (kept for tests/offline) — DEFAULT
  nats.go      // NEW: nats.Conn-backed Publish/Subscribe (JSON payloads)
internal/httpapi/server.go   // + GET /v1/subscribe (SSE)  + GET /v1/stream (all topics)
cmd/cofiswarm-zmq-bridge/main.go  // pick backend via COFISWARM_BUS=mem|nats, COFISWARM_NATS_URL
```

## PRs (smallest first)

### PR1 — NATS backend (keystone, ~½ day)
- `go get github.com/nats-io/nats.go`.
- Extract a `Backend` interface; rename the current impl `MemBackend`; add `NatsBackend`
  (`Publish` → `nc.Publish(topic, json)`, `Subscribe` → `nc.Subscribe`).
- `main.go`: select backend via env, **default `mem`** so behavior is unchanged until opted in.
- For `/v1/events` parity under NATS, feed the existing `Recent` ring from a `>` wildcard subscription.
- Result: `/v1/publish` actually fans out to NATS subscribers; HTTP surface untouched.

### PR2 — HTTP↔bus bridge (~½ day)
- `GET /v1/subscribe?topic=<subj>` as **SSE**, and `GET /v1/stream` (all topics). This is the literal
  "bridge" role — lets `cofiswarm-observer` and browsers consume the bus with no NATS client.

### PR3 — presence subjects (trivial)
- Add the diagram's control subjects to `cofiswarm-common/zmq/topics.yaml`
  (`swarm.observer.announce/goodbye/presence/alert`). The bridge only *carries* them; presence
  *logic* is a separate `cofiswarm-agent-registry` PR.

### PR4 — prove it end-to-end (~½ day)
- Make `cofiswarm-observer` subscribe (via `/v1/subscribe` or NATS) to `presence`/`alert` and render —
  demonstrating the pattern without touching any agent.

## Why it's low-risk
- Default backend stays `mem` → **zero behavior change** until `COFISWARM_BUS=nats`.
- HTTP API (`/v1/publish`, `/v1/events`, `/v1/topics`, `/healthz`) preserved verbatim.
- NATS runs **alongside** ZMQ/HTTP; `/healthz` stays for container liveness — bus presence is additive.
- The stub status means the transport is being *chosen* now, not *replaced*.

## Tests
- `nats_test.go`: Publish/Subscribe round-trip (skip-if no `nats-server`, or run one in CI).
- Keep `mem` tests as-is; add an SSE bridge integration test (publish → receive).

## Reuse what already exists
`~/observer` implements these exact semantics. Its **subject names** and **`Token` / `Presence` /
`Alert` envelopes** (`bus/subjects.py`) are the ready-made wire contract for PR3, so cofiswarm and
observer stay interoperable on the same bus.

## Downstream repos that follow once the bus is NATS
| Repo | Diagram concept | Change |
|---|---|---|
| `cofiswarm-observer` (+`-sdk`) | Observer GUI | subscribe to bus presence/alert/token streams |
| `cofiswarm-agent-registry` | Presence/registry | announce/goodbye on the bus; drop `/healthz` polling for liveness |
| `cofiswarm-backend-sdk` + `infer-*` | Drop-in models | infer servers self-announce on the bus |
| `cofiswarm-dispatch` / `orchestrate` / `gateway` | Router | dispatch over the bus with no-responders detection |
| `cofiswarm-stream-sdk` / `mode-sdk` | Token streaming | publish tokens to `…tokens.<id>` subjects |
