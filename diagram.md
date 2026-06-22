# Observer-based Local LLM Application — Architecture

A local-LLM application (llama.cpp / opencode style) built around a single always-on
**"middle man"** — a message broker + service registry that is the only mandatory process.
The **GUI talks only to the middle man**, and every component connects only to the middle man.
Communication is **fully asynchronous** (pub/sub, fire-and-forget, no heartbeat polling).
Components are **language-independent**, **fault-isolated** (one going down does not take the
system down), and **hot-pluggable** (added/removed at runtime). The GUI is an **Observer** that
reactively renders live system state from the broker's async event and presence streams.

> Preview in VS Code: open this file and press `Cmd+K V`.

## Component Architecture

```mermaid
%%{init: {"theme": "default"}}%%
graph TB
    subgraph Middleware["Middleware Layer - Central Hub"]
        MW["Auth / Validation / Rate Limit / Logging / Router"]
    end

    subgraph Interface["API Layer"]
        API["API HTTP and CLI"]
        WS["WebSocket Server"]
    end

    subgraph Core["Observer Core"]
        SUBJECT["Subject / Observable - event bus"]
    end

    subgraph Engine["Engine"]
        INF["Inference Engine - llama.cpp"]
        LLM["LLM Model"]
    end

    subgraph Capabilities["Capabilities"]
        TOOLS["Tools - coding, web"]
        PROMPTS["Prompt Store"]
        DB[("Database")]
        PAPERS["LLM Versions"]
    end

    UI["Web UI / CLI"]

    UI <--> API
    UI <--> WS

    API <--> MW
    WS <--> MW
    SUBJECT <--> MW
    INF <--> MW
    TOOLS <--> MW
    PROMPTS <--> MW
    DB <--> MW
    PAPERS <--> MW

    INF --> LLM
```

## Observer Event Flow

```mermaid
%%{init: {"theme": "default"}}%%
sequenceDiagram
    participant Client
    participant WS as WebSocket
    participant MW as Middleware
    participant Bus as Event Bus
    participant Inf as Inference Engine
    participant Tool as Tools

    Client->>WS: connect and subscribe
    WS->>MW: auth, validate, rate-limit
    MW->>Bus: register observer
    Client->>WS: prompt request
    WS->>MW: validate payload
    MW->>Bus: forward request
    Bus->>MW: dispatch to engine
    MW->>Inf: start generation
    loop streaming
        Inf-->>MW: token event
        MW-->>Bus: relay token
        Bus-->>WS: notify token
        WS-->>Client: stream token
    end
    Inf-->>MW: tool_call event
    MW->>Tool: invoke tool
    Tool-->>MW: tool_result
    MW-->>Inf: resume with result
    Inf-->>MW: done event
    MW-->>Bus: relay done
    Bus-->>WS: notify done
    WS-->>Client: complete
```

## Design Patterns

This architecture combines several classic design patterns:

| Pattern | Where it appears | Role |
|---|---|---|
| **Observer / Pub-Sub** | `Subject / Observable` event bus ↔ subscribers (`WS`, `Tools`) | Inference emits events (tokens, status, tool-calls); subscribers react in real time. The core of the design. |
| **Mediator** | `Middleware Layer (Central Hub)` | Every component talks *through* the hub instead of directly to each other, centralizing routing and keeping components decoupled. |
| **Facade** | `API Layer` (`API HTTP and CLI`, `WebSocket Server`) | One simplified entry point for clients, hiding middleware + core + engine behind it. |
| **Chain of Responsibility** | Middleware concerns: Auth → Validation → Rate Limit → Logging → Router | Each stage processes a request and passes it along the pipeline. |
| **Strategy** *(implied)* | `Inference Engine` + `LLM Versions` | Swappable backend (llama.cpp ↔ opencode ↔ remote API) behind one interface. |
| **Command** *(implied)* | Tool flow: `tool_call` → `invoke tool` → `tool_result` | Each action is packaged as an object that is dispatched, executed, and returns a result. |
| **Repository** *(implied)* | `Prompt Store`, `Database`, `LLM Versions` | Uniform data-access abstraction behind the hub. |

**Note:** Observer and Mediator do overlapping coordination work here — the Mediator (middleware)
sits between the engine and the event bus, so events relay through the hub rather than publishing
directly. This is intentional, but it makes the middleware a single chokepoint to keep an eye on.

## Standalone Services

Each piece runs as its **own independent process** with its own entry point. They do not call
each other in-process — they communicate **through the Middleware hub over a transport**
(WebSocket / HTTP / message queue), using the validated **event schema as the contract**. This
lets any component be started, stopped, scaled, or replaced on its own.

| Service | Runs standalone as | Talks to hub via | Contract |
|---|---|---|---|
| **API / CLI** | HTTP server / CLI binary | HTTP or local socket | Request schema |
| **WebSocket Server** | WS daemon | WebSocket | Event schema |
| **Middleware Hub** | Broker / router process | — (it *is* the bus) | Routes all events |
| **Observer Core** | Event-bus process | hub transport | Event schema |
| **Inference Engine** | llama.cpp / opencode worker | hub transport | Inference request/event |
| **Tools** | Per-tool worker process | hub transport | Tool call/result |
| **Prompt Store / DB / Versions** | Data service | hub transport | Repository query/response |

### Deployment View

```mermaid
%%{init: {"theme": "default"}}%%
graph LR
    UI(["UI / CLI Client"])

    HUB{{"Middleware Hub - broker + router"}}

    P_API["API Service (process)"]
    P_WS["WebSocket Service (process)"]
    P_CORE["Observer Core (process)"]
    P_INF["Inference Engine (process)"]
    P_TOOLS["Tool Workers (process)"]
    P_DATA["Data Services (process)"]

    UI <-->|HTTP / WS| P_API
    UI <-->|WS| P_WS

    P_API <-->|transport| HUB
    P_WS <-->|transport| HUB
    P_CORE <-->|transport| HUB
    P_INF <-->|transport| HUB
    P_TOOLS <-->|transport| HUB
    P_DATA <-->|transport| HUB
```

**Implications**
- Each service is independently deployable and testable in isolation (mock the hub transport).
- The contract is the **schema**, not a shared in-memory object — keep validation at every hub boundary.
- The hub must be running for the system to integrate; design it for restart/reconnect (services
  should reconnect and re-subscribe, not crash, if the hub bounces).
- Per the modular rule, each service stays in its own module/package with a thin transport adapter.

## Resilient Central Hub

The **middle man** (broker + registry) is the single always-on process. Every other piece is an
optional, independent client that connects asynchronously and **announces itself once** on connect.
There is **no heartbeat / polling** — presence is event-driven, derived from the broker's async
connect/disconnect events.

```mermaid
%%{init: {"theme": "default"}}%%
graph TB
    GUI(["Observer GUI"])

    HUB{{"MIDDLE MAN - always on - broker + registry"}}

    GUI <-->|only talks to hub| HUB

    C_API["API / CLI"]
    C_MODEL_A["LLM Model A"]
    C_MODEL_B["LLM Model B"]
    C_TOOLS["Tools"]
    C_DATA["Data Services"]
    C_NEW["New Component - joins at runtime"]

    C_API -.async.-> HUB
    C_MODEL_A -.async.-> HUB
    C_MODEL_B -.async.-> HUB
    C_TOOLS -.async.-> HUB
    C_DATA -.async.-> HUB
    C_NEW -.hot-plug.-> HUB
```

Dotted links mean "optional / may be absent" — if a component is not connected, the hub and every
other component keep running. The system never depends on all components being present.

## Dynamic Membership & Fault Isolation

All messages are asynchronous (`-)` send, `--)` async reply). A component announces on connect, a new
one can join at runtime, and two disconnect cases are handled differently: an **idle** component
dropping is silent, while a **needed** component dropping mid-operation triggers an alert to the GUI.

```mermaid
%%{init: {"theme": "default"}}%%
sequenceDiagram
    participant GUI as Observer GUI
    participant Hub as Middle Man
    participant C1 as Component (idle)
    participant C2 as Component (needed)

    C1-)Hub: announce (async)
    Hub--)GUI: presence: C1 online
    C2-)Hub: announce (async)
    Hub--)GUI: presence: C2 online

    Note over GUI,C2: A new component can join at runtime - no restart

    GUI-)Hub: request (needs C2)
    Hub-)C2: dispatch work (async)

    Note over Hub,C1: Case 1 - idle component drops
    C1--xHub: connection lost
    Hub--)GUI: presence: C1 offline (quiet)

    Note over Hub,C2: Case 2 - NEEDED component drops mid-operation
    C2--xHub: connection lost
    Hub--)GUI: ALERT: required component C2 down, operation cannot complete
```

## Kubernetes Analogy

The design mirrors a Kubernetes-style control plane — but **event-driven instead of polled**.

| Kubernetes | This design |
|---|---|
| API server + etcd (always up) | The middle man (broker + registry) |
| Pods register / are scheduled | Components connect and async-announce |
| Dashboard observes cluster state | Observer GUI renders live state |
| One pod crashing ≠ cluster down | One component down ≠ system down |
| **Liveness/readiness probes (polling)** | **Async connect/disconnect events (no polling)** |

## Middle Man Technology

Recommended broker: **NATS** — a single tiny always-on binary with async pub/sub, polyglot clients
(40+ languages), free join/leave, and native async client connect/disconnect events (no heartbeat
needed). Optional JetStream adds durability. Lighter and better-fitting for a PC than Pulsar
(cluster-heavy), Redis (weak discovery), or RabbitMQ.

This pattern is not new — existing systems implement most of it:

| System | What it already provides |
|---|---|
| **MQTT brokers** (Mosquitto, EMQX) | **Last Will & Testament**: broker auto-publishes a message when a client drops — exactly the "notify on disconnect" behavior. |
| **ROS 2 / DDS** | Independent polyglot nodes, discovery middleware, live observer graph, "liveliness" QoS that notifies when a needed publisher disappears. |
| **NATS** | Lightest broker; async presence events; you add the dependency-aware alert logic. |

The **dependency-aware alert** ("notify only if the dropped component was *needed* for in-flight work")
is app logic the middle man owns regardless of which broker is chosen.

## Configurable Model Drop-in

LLM models are **configurable drop-in components**. Each model is its own process that connects to the
middle man and **announces its metadata** (name, backend, context length, quantization, capabilities).
A models config (e.g. `models.yaml`) drives which models load; the Observer GUI renders the live set as
selectable options; each request carries a **target-model** field and the router dispatches to the
matching model component. Pattern: **Strategy** (swappable backend) + **Registry** (dynamic catalog).

```mermaid
%%{init: {"theme": "default"}}%%
graph TB
    CFG["models.yaml - configured models"]
    GUI(["Observer GUI - lists available models"])

    HUB{{"MIDDLE MAN - router + registry"}}

    M_A["Model A - llama.cpp / GGUF"]
    M_B["Model B - Ollama"]
    M_C["Model C - remote API"]

    CFG -->|load on start| M_A
    CFG -->|load on start| M_B
    CFG -->|load on start| M_C

    M_A -.announce metadata.-> HUB
    M_B -.announce metadata.-> HUB
    M_C -.announce metadata.-> HUB

    HUB -->|live model list| GUI
    GUI -->|request target-model| HUB
    HUB -->|dispatch to match| M_B
```

**Usable model components (existing tools):**

| Tool | Role as a model component |
|---|---|
| **Ollama** | Local drop-in by name; OpenAI-compatible API; loads GGUF on demand |
| **LiteLLM** | Config-driven catalog/router across many backends behind one API |
| **LocalAI** | Self-hosted, OpenAI-compatible, models defined by config |
| **vLLM / TGI** | High-throughput GPU serving; can host/switch models |

## Language Independence

Components are **polyglot** — each is a separate process/binary in any language, sharing **no library
or runtime**, only the broker's wire protocol and an agreed **message schema**. Define and version the
schema centrally (JSON Schema or protobuf) so any-language component can validate against it at the
broker boundary. The contract is the message on the wire, not shared code.

> Default: JSON Schema for simplicity; protobuf if you later want compact, strongly-typed messages.
> JetStream (NATS durability) is optional and not required for the core design.

---

# As-Built Implementation

The sections above are the original design. This section documents what was actually built and is
running. It started as the **two pieces novel vs cofiswarm** — a single NATS broker "middle man" and
event-driven (no-heartbeat) presence — plus the observer GUI, recording, and bridges. Since then the
[OBSERVER-PLAN](OBSERVER-PLAN.md) conversion has made the **bus the coordination spine** rather than a
read-only bridge: a versioned schema contract (S0), an API gateway facade (S0), a bus-native registry
and orchestrator (S1), and a complete model lane with MLX spawn + lifecycle (S2). The cofiswarm
HTTP services are now optional fallbacks, not dependencies.

## As-Built Components

```mermaid
%%{init: {"theme": "default"}}%%
graph TB
    BROWSER(["Browser / CLI"])

    subgraph GUIp["GUI process (aiohttp)"]
        GUI["gui/server.py - WS bridge + /history + /stats"]
    end
    subgraph GWp["API gateway :8100 (facade)"]
        GW["gateway/ - app · bus_proxy · cli (HTTP+CLI -> bus)"]
        MWARE["middleware: auth -> rate-limit -> logging -> router"]
    end

    NATS{{"NATS broker :4222 - the middle man transport"}}

    MM["middleman.py - router + presence + roster + hello + schema gate"]
    SCHEMA[("bus/contracts + bus/schema/*.json - versioned contract")]
    REG["registry component - agents/modes/roles/topology"]
    LIFE["lifecycle component - convert · vllm.start"]
    KVP["kvpool (Go) - .kv.admit/evaluate/policy"]
    SLOT["slot-manager (Go) - .slots.pressure/evict"]
    LAUN["launcher (Go) - .launcher.configure/status"]
    DATA["data service - .data.* (memory/cache/history/rag/logs/models)"]
    TOOLS["tool workers - .tools.calc/web (Command)"]
    OBS["observability - .metrics / .health.agents / .config"]
    REC["recorder - runs/alerts -> .run/history.jsonl"]
    ECHO["echo model component"]

    subgraph Cofi["cofiswarm bridge (run_cofiswarm.py)"]
        AG["13 agent components - per-server concurrency gate"]
    end
    subgraph Modes["modes (run_modes.py) - native bus orchestration"]
        MO["4 Orchestrator components - flat/pipeline/cascade/router"]
    end

    LLAMA["llama.cpp / MLX servers :8083-8087"]
    DISP["cofiswarm-dispatch :8010 (SSE) - optional --bridge fallback"]

    BROWSER <-->|WebSocket| GUI
    BROWSER <-->|HTTP / CLI| GW
    GW <-->|request| NATS
    GUI <-->|pub/sub + request| NATS
    MM <--> NATS
    MM -.validates.-> SCHEMA
    REG <--> NATS
    LIFE <--> NATS
    KVP <--> NATS
    SLOT <--> NATS
    LAUN <--> NATS
    LAUN -->|spawn| LLAMA
    SLOT -->|evict slots| LLAMA
    DATA <--> NATS
    TOOLS <--> NATS
    OBS <--> NATS
    MO -->|.tools.* call| TOOLS
    GW -->|.metrics/.health/.config| OBS
    REC -->|subscribe| NATS
    ECHO <--> NATS
    AG <--> NATS
    MO <--> NATS
    MO -->|.registry.modes| REG
    MO -->|.model.* fan-out| AG
    AG -->|/v1/chat/completions stream| LLAMA
    MO -.->|legacy| DISP
```

## Subject Reference (`swarm.observer.*`)

| Subject | Direction | Payload | Purpose |
|---|---|---|---|
| `.announce` | component → bus | `Announce` | join / re-announce |
| `.goodbye` | component → bus | `Goodbye` | graceful leave |
| `.presence` | middleman → observers | `Presence` | online/offline updates |
| `.alert` | middleman → observers | `Alert` | needed-component-down / timeout |
| `.request` | observer → middleman | `InferRequest` | inference (req/reply) |
| `.roster` | observer → middleman | `RosterReply` | snapshot for late joiners |
| `.cancel` | observer → models | `Cancel` | abort an in-flight request |
| `.hello` | middleman → components | `Hello` | restart → re-announce |
| `.model.<name>` | middleman/orchestrator → model | `InferRequest` | per-model dispatch (req/reply) |
| `.tokens.<request_id>` | model → observers | `Token` | streamed tokens + final usage |
| `.registry.{agents,modes,roles,topology}` | caller → registry | `*Query` / `*Reply` | catalog (S1) |
| `.lifecycle.{convert,vllm.start}` | caller → lifecycle | `*Request` / `JobReply` | model lifecycle (S2) |
| `.kv.{admit,evaluate,policy}` | caller → kvpool (**Go**) | `Kv*Request` / `Kv*Reply` | KV admission/pressure/policy (S3) |
| `.slots.{pressure,evict}` | caller → slot-manager (**Go**) | `Pressure*` / `Evict*` | KV pressure + eviction (S3) |
| `.launcher.{configure,status}` | caller → launcher (**Go**) | `ConfigureRequest` / `*Reply` | spawn servers (RoPE/YaRN/KV-quant); async, status-polled (S3) |
| `.data.<resource>` | caller → data service | `DataQuery` / `DataReply` | Repository: memory/cache/history/rag/logs/models (S4) |
| `.tools.<tool>` | orchestrator → tool worker | `ToolCall` / `ToolResult` | Command: calc/web, folded back into generation (S4) |
| `.metrics` · `.health.agents` · `.config` | gateway → observability | `MetricsReply` / `HealthReply` / `Config*` | meta/observability (S5) |

All design subjects now have handlers. Every envelope carries
`schema_version`; the middleman and gateway reject unsupported majors. The exported JSON Schema
(`bus/schema/*.json`, 47 envelopes) is the cross-language contract — the Go S3 components are the first
non-Python consumers, proving it's wire-level, not in-process.

## Module Map

| Path | Responsibility |
|---|---|
| `bus/subjects.py` | subjects + core Pydantic envelopes (mirrors cofiswarm `agent.json`) |
| `bus/contracts/` | versioned envelopes: `base` · `registry` · `lifecycle` · `resource` · `data` · `tools` · `meta` |
| `bus/schema_export.py` | emit `bus/schema/*.json` from every envelope (`--check` drift guard) |
| `bus/component.py` | `ServiceComponent` — generic request/reply capability base |
| `bus/nats_bus.py` | async NATS wrapper; request surfaces no-responders/timeout |
| `bus/presence.py` | event-driven presence registry (no heartbeat) |
| `bus/middleman.py` | router; slow≠down handling; roster; hello; schema-major gate |
| `gateway/` | `app` · `bus_proxy` · `cli` · `middleware` (facade + auth→rate-limit→logging chain) |
| `components/registry.py` | agent/mode/role/topology catalog (`.registry.*`) |
| `components/lifecycle.py` | model convert + vllm start (`.lifecycle.*`) |
| `components/data_service.py` | Repository tier (`.data.*`) |
| `components/tools/` | `base` (ToolWorker) · `calc` · `web` — Command workers (`.tools.*`) |
| `components/observability.py` | metrics / health.agents / config (`.metrics`,`.health.agents`,`.config`) |
| `adapters/cofiswarm_model.py` | model component; sessions; cancel; EchoBackend |
| `adapters/llama_backend.py` | llama/MLX OpenAI-compatible streaming + usage + per-server gate |
| `adapters/mlx_backend.py` | MLX spawn (TurboQuant KV cap) + `/v1/models` probe + streaming |
| `adapters/orchestrator.py` | native bus fan-out (flat/pipeline/cascade/router) + tool calls — no HTTP |
| `adapters/dispatch_backend.py` | legacy cofiswarm mode bridge via dispatch SSE (`--bridge` only) |
| Go repos | `cofiswarm-{kvpool,slot-manager,launcher}` — each `internal/bus` + `-bus` flag (S3) |
| `recorder/{store,service,stats}.py` | JSONL history + per-model aggregates |
| `gui/server.py`, `gui/index.html` | aiohttp NATS↔browser bridge + UI |
| `run_*.py` | middleman·model·registry·lifecycle·data·tools·observability·cofiswarm·modes·recorder·gui·gateway |
| `scripts/{start_stack,supervise}.sh` | tmux startup + per-service auto-restart (capped backoff) |
| `tests/` | 114 broker-free tests (pytest) + Go `internal/bus` tests in the 3 S3 repos |

## Operations

- All services run in tmux session **`observer`** (windows: nats, middleman, echo-fast, registry,
  lifecycle, data, tools, observ, recorder, gui, gateway, kvpool, slot-manager, launcher, cofiswarm,
  modes), each under `scripts/supervise.sh` (auto-restart, capped backoff). The Go resource tier
  (`kvpool`, `slot-manager`, `launcher`) is built from its own repos:
  `(cd <repo> && go build -o bin/<repo> ./cmd/<repo>)`.
- **Reboot survival:** launchd agent `~/Library/LaunchAgents/com.observer.stack.plist` runs
  `scripts/start_stack.sh` at login.
- **GUI:** http://127.0.0.1:8099 — grouped roster, live token streaming + metrics, Stop, sessions,
  History (replay), Stats. **Gateway:** http://127.0.0.1:8100 — `/api/version`, `/api/swarm/status`
  (also `python -m gateway.cli status`).
- **Tests:** `python3 -m pytest tests/ -q` (no broker/network needed).
- **Schema:** `python3 -m bus.schema_export` regenerates `bus/schema/*.json`; `--check` guards drift.
- **Resilience notes:** a *slow* model (TimeoutError) stays registered; only a *no-responder* is
  deregistered. The middleman is in-memory and re-populates via `hello` on restart. Agents sharing one
  llama server serialize through a per-server semaphore (`PER_SERVER_CONCURRENCY`).

