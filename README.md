# observer

A local-LLM console built around a single always-on **"middle man"** (a NATS broker) with
**event-driven, no-heartbeat presence**. It bridges the live [cofiswarm](../cofiswarm) system —
13 agents (backed by real llama.cpp/MLX servers) and 4 orchestration modes — onto one async bus,
and gives you a web GUI with live token streaming, multi-turn chat, history, and per-model stats.

> Architecture & design rationale: see **[diagram.md](diagram.md)** (conceptual design + As-Built section).

## What it is
The two pieces that are **novel vs cofiswarm** (which uses HTTP + ZeroMQ + SSE with `/healthz` polling):

1. **A single always-on NATS broker — the middle man.** Every component speaks async pub/sub +
   request/reply over one connection. Subjects live in the `swarm.observer.*` namespace.
2. **Event-driven, no-heartbeat presence.** Online from `announce`, offline from `goodbye`; a *needed*
   component that's gone is caught at dispatch via NATS no-responders → alert. A *slow* model
   (timeout) is **not** evicted — only a genuinely absent one is.

Everything else — agents, backends, orchestration modes — is reused from cofiswarm via thin bridges.

## Quick start
```bash
pip install -r requirements.txt          # nats-py, pydantic, pyyaml (+ aiohttp for GUI)
bash scripts/start_stack.sh              # brings up the whole stack in tmux session 'observer'
open http://127.0.0.1:8099               # the Observer GUI
```
`start_stack.sh` launches (each supervised, auto-restarting): `nats` broker · `middleman` · an `echo`
model · `recorder` · `gui` · `cofiswarm` (13 agents) · `modes` (4 orchestrations). A launchd agent
(`~/Library/LaunchAgents/com.observer.stack.plist`) re-runs it at login for reboot survival.

## GUI (http://127.0.0.1:8099)
- **Grouped roster** — Orchestrations / Agents / Demo, live online status.
- **Streaming** — pick a model or a `swarm-*` orchestration; tokens stream live.
- **Metrics** — time-to-first-token, exact tokens + tok/s on completion.
- **Stop** — cancel an in-flight request. **New chat** — fresh multi-turn session.
- **History** — replay past runs. **Stats** — per-model runs / errors / avg latency / tok-per-sec.

## Layout
```
bus/        subjects.py · nats_bus.py · presence.py · middleman.py     (broker + presence core)
adapters/   cofiswarm_model.py · llama_backend.py · dispatch_backend.py (model + bridges)
recorder/   store.py · service.py · stats.py                            (history + aggregates)
gui/        server.py · index.html                                      (aiohttp NATS↔browser bridge)
run_*.py    middleman · model · cofiswarm · modes · recorder · gui      (entry points)
scripts/    start_stack.sh · supervise.sh                               (tmux startup + auto-restart)
tests/      31 broker-free pytest tests
```

## Bridges
- **Agents** (`run_cofiswarm.py`): each `cofiswarm-agent-registry` agent becomes a bus model component
  backed by its live llama.cpp/MLX server (`/v1/chat/completions` streaming). Agents sharing a server
  serialize through a per-server concurrency gate.
- **Orchestrations** (`run_modes.py`): flat/pipeline/cascade/router via `cofiswarm-dispatch` SSE
  (`/api/architect/stream`), surfaced as `swarm-<mode>` with agent/stage markers.

## Operate
```bash
tmux attach -t observer                  # watch all windows (Ctrl-b d to detach)
tail -f .run/{middleman,gui,cofiswarm}.log
python3 -m pytest tests/ -q              # run tests (no broker needed)
tmux kill-session -t observer            # stop everything (leaves nothing else touched)
```

## Notes
- Read-only toward cofiswarm: the bridges are HTTP/SSE clients; cofiswarm itself is untouched.
- The broker (4222) is owned by this stack; cofiswarm uses ZeroMQ, not NATS.
- Per the modular rule, each module is single-responsibility and within the LOC budget.
