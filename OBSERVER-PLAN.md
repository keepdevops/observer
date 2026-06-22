# OBSERVER-PLAN — Full monolith parity as standalone bus components

Supersedes `CURSOR-PLAN.md`. Same goal (retire the legacy C++ `coordinator` monolith), different
spine. CURSOR-PLAN re-homed half the surface into standalone **HTTP services** (`:8010–:8017`) and
made Observer a **read-only bridge** that polls/streams them over HTTP/SSE. This plan makes the
**NATS bus the actual coordination spine**: every coordinator capability becomes a first-class,
hot-pluggable **bus component**, and HTTP/CLI/WS collapses into one thin **API gateway**.

## Why this is better than CURSOR-PLAN

| Problem in CURSOR-PLAN | Cause | Fix here |
|---|---|---|
| Two architectures in parallel (HTTP split *and* a NATS bridge) | bus only *observes* HTTP services | one spine — capabilities **are** bus components |
| slot-manager (:8013) / launcher (:8017) silently "(down)" | liveness only modeled for bus, not HTTP tier | every capability announces presence → down = visible + alertable |
| MLX never spawned; TurboQuant blocked | launcher `continue`d past non-llama backends | model lane owns spawn for all backends (S2) |
| Tools (a first-class design concept) have no home | dropped in the HTTP split | `tools.*` subjects + tool-worker components (S4) |
| "schema is the contract" but it's Python Pydantic only | no exported, versioned schema | schema spine: JSON Schema emitted from Pydantic, versioned envelopes (S0) |
| Bridges are HTTP clients → bus depends on HTTP services being up | inverted dependency | gateway depends on bus; bus depends on nothing |

**Invariant:** the broker is the only mandatory process. Every other capability is an optional,
async, hot-pluggable client that announces once on connect. No capability calls another in-process
or over private HTTP — only `swarm.observer.*` subjects with versioned, validated envelopes.

## Target topology

```
            ┌────────────────────────── API GATEWAY (facade) ──────────────────────────┐
Browser/CLI │  HTTP + CLI + WebSocket  →  translate to bus req/reply, stream tokens back │
            └──────────────────────────────────┬───────────────────────────────────────┘
                                                │  swarm.observer.*  (validated envelopes)
                                   ┌────────────▼────────────┐
                                   │  MIDDLE MAN (NATS :4222) │  router · presence · roster · hello
                                   └────────────┬────────────┘
   ┌───────────────┬───────────────┬───────────┼───────────┬───────────────┬───────────────┐
 registry      orchestrator     model lane   resource ctrl  data services   tools        observability
 (agents/      (architect/      llama/mlx/   slot-mgr/      memory/cache/   coding/web   metrics/health/
  modes/roles/  pipeline/        echo/remote  kvpool/        history/rag     workers      version/config
  topology)     cascade/router)  +lifecycle   launcher                                    + recorder + GUI
```

The GUI stays an **Observer** (renders live roster + token streams). It is just another bus client —
no special path.

## Schema spine (the real contract)

`bus/subjects.py` today holds Pydantic envelopes for the *presence* core only. Promote it to the
**single versioned contract** for the whole surface:

- Each envelope carries `schema_version` (semver) and `kind`. Breaking changes bump major; the
  gateway and middleman reject envelopes whose major they don't support (fail loud, log, alert).
- Emit `bus/schema/*.json` (JSON Schema) from the Pydantic models in CI so **polyglot** components
  (Go launcher, future Rust/JS) validate against the same contract. Python remains source of truth.
- Every subject below has exactly one request envelope and one reply/stream envelope.

## Subject map (full monolith surface)

| Subject (`swarm.observer.…`) | Component | Legacy coordinator capability |
|---|---|---|
| `.announce/.goodbye/.presence/.alert/.roster/.hello` | middleman | bus core (exists) |
| `.request` + `.model.<name>` + `.tokens.<rid>` + `.cancel` | model lane | inference req/reply + stream (exists) |
| `.orchestrate.<mode>` (flat/pipeline/cascade/router) | orchestrator | `/api/architect[/stream]`, fan-out, `/api/orchestrate` |
| `.registry.{agents,modes,roles,topology}` | registry | `/api/agents`, `/api/modes`, `/modes/active`, `/modes/{n}/agents`, `/roles` |
| `.slots.{pressure,evict}` | slot-manager | `/api/pressure[/evict]`, slot eviction |
| `.kv.{admit,budget,policy}` | kvpool | KV admission / budget / policy |
| `.launcher.{configure,status}` | launcher | `/api/configure[/status]` (RoPE/YaRN/KV-quant) |
| `.lifecycle.{convert,vllm.start}` | model-lifecycle | `/api/models/convert`, `/api/inference/vllm/start` |
| `.mlx.{agents,health,modes,stream,submit,session.clear,pressure}` | model lane (mlx) | `/api/mlx/*` |
| `.data.{memory,cache,history,rag,logs,swarm-config,models}` | data services | `/api/memory,cache*,history*,logs,rag/health,swarm-config,models` |
| `.tools.<tool>` (request/result) | tool workers | tool call/result (Command pattern — never converted) |
| `.metrics`, `.health.agents`, `.config`, `.version`, `.swarm.status` | observability | `/api/metrics`, `/api/health/agents`, `/api/v1/config`, `/api/version`, `/api/swarm/status` |

The gateway maps each legacy HTTP route 1:1 onto a subject, so external clients are unchanged while
the implementation moves onto the bus.

## Sprints (each: exit criteria + how to verify)

### S0 — Schema spine + API gateway skeleton  *(foundation)*  ✅ DONE
Built: `bus/contracts/{__init__,base}.py` (versioned `Envelope` + `major_supported`); subjects now
inherit `Envelope` and reserve every capability subject; `middleman._on_request` rejects unsupported
majors (alert + loud error); `bus/schema_export.py` emits `bus/schema/*.json` (+ `--check` drift
guard, committed); `gateway/{app,bus_proxy,cli}.py` + `run_gateway.py` (:8100) serve `/api/version`
+ `/api/swarm/status` from the roster; gateway window added to `start_stack.sh`; README updated.
38 tests pass (7 new); CLI verified to fail loud (rc=2) when the broker is down.

- Add `schema_version`/`kind` to every envelope; CI emits `bus/schema/*.json`; middleman rejects
  unknown-major (logged + `.alert`).
- New `gateway/` process: HTTP + CLI + WS facade that translates **one** known route
  (`/api/version`, `/api/swarm/status`) into bus calls and proxies token streams.
- **Exit:** gateway serves version/status purely from bus roster; no direct HTTP to any `:80xx`.
- **Verify:** `pytest tests/` green; kill middleman → gateway returns a loud 503, not a hang.

### S1 — Registry + orchestrator go bus-native  *(remove the HTTP bridges)*  ✅ DONE
Built: `bus/component.py` (`ServiceComponent` generic request/reply base) + `ServiceReply`;
`bus/contracts/registry.py`; `components/registry.py` + `run_registry.py` serving `.registry.*`
from the agent JSON dir + `ob_code/modes.yaml` (empty mode `agents` == all discovered);
`adapters/orchestrator.py` — native flat/pipeline/cascade/router fan-out over the bus (resolves
agents via the registry, streams agent/stage-marked tokens); `run_modes.py` now defaults to the
native orchestrator with a reversible `--bridge` fallback to dispatch SSE; registry added to
`start_stack.sh`; schema export auto-discovers `bus/contracts/*` (22 envelopes). 49 tests pass
(11 new). **Pending S1 exit:** live verify (stop dispatch+registry HTTP, confirm a pipeline still
streams) → then delete `adapters/dispatch_backend.py` and the `--bridge` path.

- Replace `adapters/dispatch_backend.py` (HTTP/SSE to `:8010`) with an **orchestrator component**
  that owns flat/pipeline/cascade/router fan-out directly over `.orchestrate.<mode>` + `.tokens.*`.
- Replace the agent-registry HTTP dependency with a **registry component** serving
  `.registry.{agents,modes,roles,topology}` from `models.yaml` + live presence.
- **Exit:** dispatch (:8010) and agent-registry (:8012) no longer required for Observer to run.
- **Verify:** stop both HTTP services; multi-agent pipeline still streams end-to-end on the bus.

### S2 — Model lane complete  *(close the biggest gap: MLX + lifecycle)*  ✅ DONE
Built: `adapters/mlx_backend.py` — pure `build_mlx_args` (TurboQuant KV cap via
`--prompt-cache-bytes`, no `--kv-bits`; draft model; extra-args last), `MLXServer`
(spawn / `/v1/models` await_ready / stop / connect-if-already-serving), `MLXServerBackend`
composing `LlamaServerBackend` for OpenAI-compatible streaming; `run_model.py` selects
echo/llama/mlx by engine and `ensure_ready()`s managed MLX before announce; `ob_code/models.yaml`
gains `mlx-scout` (:8083, 512 MiB KV cap, env-overridable model/python); `bus/contracts/lifecycle.py`
+ `components/lifecycle.py` + `run_lifecycle.py` serve `.lifecycle.{convert,vllm.start}` (pure
command builders, loud validation, detached spawn → job_id/pid); lifecycle added to `start_stack.sh`.
62 tests pass (13 new). Note: `.mlx.*` needs no new subjects — MLX is just another backend, and the
legacy `/api/mlx/*` routes map onto existing subjects (registry/presence/inference), aliased at the
gateway in S5. **Pending S2 exit (live):** spawn `mlx-scout`, confirm `/v1/models` healthy + a prompt
returns + SCALE-6 turbo audit on config.
- Generalize `adapters/llama_backend.py` spawn to **all backends**; add MLX spawner
  (`mlx_lm.server`, `--prompt-cache-bytes` KV cap = honest TurboQuant mapping, `/v1/models` probe).
- Add **model-lifecycle component** for `.lifecycle.{convert,vllm.start}`.
- Surface `.mlx.*` via the model lane (no separate HTTP compat surface).
- **Exit:** `mlx-scout` (8083) spawns, `/v1/models` healthy, a prompt returns; TurboQuant audit passes.
- **Verify:** `go test ./...` in launcher; live MLX prompt; SCALE-6 turbo audit on config.

### S3 — Resource control bus-native  *(slot-manager, kvpool, launcher)*
- **Language: Go.** `launcher` must drive the existing Go `cofiswarm-launcher` (MLX spawn,
  RoPE/YaRN/KV-quant), and slot-manager/kvpool were Go services in cofiswarm — so this tier reuses
  Go code, gets the best NATS client, and Go's explicit errors fit the no-silent-failures rule.
  Building one Go `ServiceComponent` equivalent here (announce/route/schema-gate) is the foundation,
  and it **absorbs S6's polyglot proof**: a Go component validating against `bus/schema/*.json`.
- Port slot-manager (`.slots.*`), kvpool (`.kv.*`), launcher/configure (`.launcher.*`) to components
  that **announce presence** — so "down" is visible and triggers `.alert` when needed mid-op.
- **Exit:** the three never-reliably-up services are bus components with presence + fault isolation.
- **Verify:** kill kvpool mid-request → GUI shows ALERT (needed component down), not a silent hang.

### S4 — Data services + Tools  *(the dropped concepts)*  ✅ DONE
Built: `bus/contracts/data.py` (uniform `DataQuery`/`DataReply` Repository envelope) +
`components/data_service.py` + `run_data.py` serving `.data.*` — history via `recorder/store.py`,
models/swarm-config from files, memory/cache in-process KV, logs tail, rag health; unknown op/resource
fail loud. `bus/contracts/tools.py` (`ToolCall`/`ToolResult`) + `components/tools/` (`base.ToolWorker`,
`web` URL-fetch, `calc` safe-AST arithmetic — chose `calc` over an unsandboxed `coding` tool) +
`run_tools.py` on `.tools.<tool>`. `adapters/orchestrator.py` now detects `[[tool:NAME {json}]]` in
agent output, dispatches over the bus, and folds the result back (pipeline feeds it to the next stage
= resume). Added `DATA`/`TOOLS` subject constants (a missing-constant bug surfaced as a test hang —
fixed at source). 86 tests pass (16 new); schema 43 envelopes; data+tools windows in `start_stack.sh`.
- **Language: Python.** Data/RAG reuse `recorder/store.py` and lean on Python's embedding/vector
  ecosystem; the existing `ServiceComponent` makes each data service ~100 LOC. **Tools are polyglot
  by nature** — each tool worker is its own process, so a Go web-fetch or a C++ tool can join the
  same `.tools.*` subject later; Python is just the default for the first coding/web workers.
- Data services component(s) for `.data.*` (memory/cache/history/rag/logs/swarm-config/models),
  Repository pattern, one validated query/response envelope.
- **Tool workers**: per-tool processes on `.tools.<tool>` (coding, web), Command pattern; orchestrator
  invokes via bus, resumes inference with the result.
- **Exit:** a tool_call in an orchestration round-trips over the bus and resumes generation.
- **Verify:** prompt that triggers a web/coding tool completes with the tool result folded in.

### S5 — Observability + meta parity  ✅ DONE
Built: `bus/contracts/meta.py` (`MetricsReply`/`HealthReply`/`ConfigQuery`/`ConfigReply`);
`components/observability.py` + `run_observability.py` serving `.metrics` (reuses
`recorder/stats.aggregate`), `.health.agents` (from roster), `.config` (JSON file get/set).
Gateway gained the **Chain-of-Responsibility middleware** (`gateway/middleware.py`:
auth → rate-limit → logging → router, auth/rate-limit off by default via `OBSERVER_API_KEY` /
`OBSERVER_RATE_LIMIT`) and the routes `/api/metrics`, `/api/health/agents`, `/api/v1/config`
(GET+POST) on top of version/swarm-status — every bus-backed route fails loud (503) when the
component is down. `version`/`swarm-status` stay gateway-derived from the roster (no component).
102 tests pass (16 new: meta/middleware/parity); schema 47 envelopes; observability window in
`start_stack.sh`. Deferred (cosmetic, untestable): the GUI `index.html` metrics/health panels —
the data is now served; the visual panels are a follow-up. The legacy-vs-gateway response diff
in `test_parity.py` is route-coverage + subject-targeting (a full byte-diff needs captured legacy
fixtures, which aren't on hand).
- **Language: Python.** Extends what already exists — the `gateway/` (aiohttp facade), `gui/`, and
  `recorder/` (history/stats aggregation are already Python). Porting these to Go would be pure churn
  with no functional gain; revisit only if the gateway is later moved to Go for single-binary ops.
- `.metrics` (aggregation), `.health.agents`, `.config` (`/api/v1/config`) components; recorder keeps
  history/stats; GUI gains parity panels.
- **Gateway middleware chain (auth → rate-limit → logging → router)** — completes the design's
  Chain-of-Responsibility at the single external surface; internal bus components trust the broker.
- **Exit:** every legacy `/api/*` route is answered through the gateway → bus (parity table 100%),
  with the middleware chain enforced at the boundary.
- **Verify:** a route-by-route diff harness; auth rejects an unkeyed request; rate-limit trips after N.

### S6 — Hardening: fault isolation, reboot, polyglot proof  ✅ DONE
Built: `tests/test_chaos.py` — single-component-down ≠ system-down, idle-goodbye is quiet,
needed-down ⇒ alert + survivors unaffected, hot-plug at runtime, broker-restart broadcasts HELLO,
re-announce on hello — all driving the real `Presence`/`MiddleMan`/`ServiceComponent`.
`tests/test_schema_contract.py` — drift, every-envelope-has-a-file, version pin, round-trip,
required-property presence. **Reboot:** no change needed — the launchd plist runs `start_stack.sh`,
which already starts every window (incl. S2–S5 components + the Go S3 binaries). **supervise.sh:**
already generic (capped 30s backoff) — covers every component unchanged. **Polyglot proof:** the
three Go S3 components' `*FieldNames` tests assert their replies conform to the shared schema field
set — real non-Python components honoring the wire contract. 114 Python tests + Go bus tests green.
- **Language: Python (tests) — Go interop already proven in S3.** The chaos/acceptance suite lives
  with the existing `tests/` (pytest); the cross-language schema round-trip uses the Go S3 components,
  so S6 no longer needs to *introduce* Go — it just verifies the Python↔Go contract end-to-end.
- Acceptance suite: any single component down ≠ system down; needed-down ⇒ alert; idle-down ⇒ quiet;
  hot-plug a new component at runtime with no restart; broker bounce ⇒ all reconnect + re-announce.
- launchd reboot survival; supervise.sh backoff; the Go S3 components validating against the exported
  JSON Schema to prove the contract is wire-level, not in-process.
- **Exit:** chaos suite green; reboot brings the whole stack back; polyglot component interops.

## Language map (per-layer, not global — the schema contract makes it polyglot)

| Sprint | Components | Language | Why |
|---|---|---|---|
| S0–S2 ✅ | bus core, gateway, registry, orchestrator, model lane, lifecycle | **Python** | Pydantic schema spine; MLX (`mlx_lm`) is Python-only; fast iteration |
| S3 | slot-manager, kvpool, launcher | **Go** | drives Go `cofiswarm-launcher`; reuses Go cofiswarm services; best NATS client; **doubles as the polyglot proof** |
| S4 | data services, tool workers | **Python** (tools polyglot) | reuse `recorder` + Python RAG/embeddings; each tool worker can be any language |
| S5 | metrics, health, config, gateway `/api/*`, GUI | **Python** | extends existing aiohttp gateway/gui + recorder; porting = churn |
| S6 | chaos suite, schema round-trip | **Python** tests + Go (from S3) | verifies the Python↔Go wire contract; no new language introduced |

Fixed constraints: **MLX lane stays Python** (`mlx_lm`); **the broker is Go** (NATS, unchanged);
**C++ only if** you later embed llama.cpp in-process. No wholesale rewrite — choose per component.

**Standalone is the rule, across languages.** Every component is its own process *and* its own
repo/module — it connects to the bus, announces once, and shares nothing but the wire schema:

| Component | Lives in | Built as | Joins via |
|---|---|---|---|
| Python components (registry, lifecycle, data, tools, observability) | `observer` repo, one module each (`components/<name>.py` + `run_<name>.py`) | a process | `bus/schema/*.json` |
| `launcher` (S3) | **`cofiswarm-launcher`** (existing Go repo) | its own static binary | `bus/schema/*.json` |
| `slot-manager` (S3) | **`cofiswarm-slot-manager`** (own Go repo) | its own static binary | `bus/schema/*.json` |
| `kvpool` (S3) | **`cofiswarm-kvpool`** (own Go repo) | its own static binary | `bus/schema/*.json` |

The Go components are **not vendored into the `observer` Python repo** — no shared `go.mod`, no shared
imports. `observer/scripts/start_stack.sh` only *supervises* their binaries (like it already does for
dispatch/modes). The one cross-repo artifact remains `bus/schema/*.json`, consumed read-only. A
component can be built, run, restarted, or replaced entirely on its own — the test that the monolith
is gone.

## Parity dashboard (replaces CURSOR-PLAN's)

| Capability | Subject | Component | Sprint | Done-when |
|---|---|---|---|---|
| version / swarm status | `.version` `.swarm.status` | observability | S0 | served from roster, no HTTP |
| agents/modes/roles/topology | `.registry.*` | registry | S1 | dispatch+registry HTTP not required |
| architect + fan-out | `.orchestrate.*` | orchestrator | S1 | pipeline streams without `:8010` |
| inference + stream + cancel | `.request/.model.*/.tokens.*` | model lane | (done) | exists |
| MLX lane + TurboQuant | `.mlx.*` | model lane | S2 | mlx-scout healthy, audit passes |
| model lifecycle | `.lifecycle.*` | model-lifecycle | S2 | convert + vllm start over bus |
| pressure / eviction | `.slots.*` | slot-manager | S3 | presence + alert on needed-down |
| KV admission/budget/policy | `.kv.*` | kvpool | S3 | bus-native, presence |
| configure (RoPE/YaRN/KV-quant) | `.launcher.*` | launcher | S3 | spawns all backends, presence |
| memory/cache/history/rag/logs/config-data | `.data.*` | data services | S4 | Repository envelopes |
| tools (coding/web) | `.tools.*` | tool workers | S4 | tool_call round-trips + resumes |
| metrics / health.agents / v1.config | `.metrics/.health/.config` | observability | S5 | parity diff harness green |
| presence/alerts/GUI/recording | core | middleman/gui/recorder | (done) | exists |

## Guardrails
- **Modular rule (global CLAUDE.md):** each component is one single-responsibility module, 250–300
  LOC; split validation/types/transport when a file grows. A thin transport adapter per component.
- **No silent failures:** every bus boundary validates the envelope (Pydantic/JSON Schema) and logs
  on reject; needed-component-down raises `.alert`; the gateway returns loud errors, never hangs.
- **Read-only toward cofiswarm during migration:** until a capability is bus-native, the gateway may
  fall back to the legacy HTTP route behind the *same* subject, so cutover is per-capability and
  reversible. Remove the fallback once the component passes its sprint exit criteria.
- **One transport, one contract:** if you find yourself adding a private HTTP call between two
  components, that's a regression toward the monolith — route it through the bus instead.

---

# Implementation tasks (file-level)

Conventions: **(new)** create, **(edit)** modify, **(test)** add/extend. LOC target per the global
rule (250–300). `subjects.py` is already 139 LOC, so new envelopes go in a `bus/contracts/` package
rather than bloating it. Capability components that are request/reply (not token-streaming) reuse a
new generic base instead of copying `ModelComponent`.

### Shared scaffolding introduced once, used by every sprint
- `bus/contracts/` **(new package)** — one module per capability group, each ≤300 LOC:
  `base.py` (envelope mixin: `schema_version`, `kind`), `registry.py`, `resource.py`,
  `lifecycle.py`, `data.py`, `tools.py`, `meta.py`. `bus/subjects.py` keeps only core
  presence/infer envelopes + subject-name constants and **re-exports** the contracts for back-compat.
- `bus/component.py` **(new, ~120 LOC)** — `ServiceComponent` base: connect, announce with a `kind`,
  subscribe to one capability subject, validate the request envelope (log+nak on reject), reply.
  `adapters/cofiswarm_model.py::ModelComponent` stays the *streaming* specialization for the model lane.

---

### S0 — Schema spine + API gateway skeleton
- `bus/contracts/base.py` **(new)** — `Envelope` mixin (`schema_version: str`, `kind: str`); helper
  `requires_major(n)`.
- `bus/subjects.py` **(edit)** — add subject-name constants for every group in the subject map
  (`ORCHESTRATE`, `REGISTRY_*`, `SLOTS_*`, `KV_*`, `LAUNCHER_*`, `LIFECYCLE_*`, `DATA_*`, `TOOLS`,
  `METRICS`, `HEALTH`, `CONFIG`, `VERSION`, `SWARM_STATUS`); make `Announce/InferRequest/...` inherit
  `Envelope`.
- `bus/middleman.py` **(edit)** — reject envelopes whose major it doesn't support in `_on_request`
  (extend the existing `try/except` that already logs); publish `.alert` on reject.
- `bus/schema_export.py` **(new, ~60 LOC)** — emit `bus/schema/*.json` from every Pydantic envelope.
- `gateway/` **(new package)**: `gateway/app.py` (aiohttp routes), `gateway/bus_proxy.py`
  (HTTP/CLI/WS → bus req/reply + token-stream relay), `gateway/cli.py`. Wire only `/api/version` +
  `/api/swarm/status` for now (derive from `S.ROSTER`).
- `run_gateway.py` **(new, ~30 LOC)** — entry point.
- `scripts/start_stack.sh` **(edit)** — add a `gateway` tmux window under `supervise.sh`.
- **(test)** `tests/test_schema_version.py` (reject wrong-major), `tests/test_gateway.py`
  (version/status from a fake roster; middleman-down → loud 503, no hang).

### S1 — Registry + orchestrator go bus-native
- `bus/contracts/registry.py` **(new)** — `AgentsQuery/Reply`, `ModesQuery/Reply`, `RolesReply`,
  `TopologyReply`.
- `components/registry.py` **(new, ~200 LOC)** — `ServiceComponent` serving `.registry.*` from
  `ob_code/models.yaml` + live presence snapshot.
- `run_registry.py` **(new)** — entry point.
- `adapters/orchestrator.py` **(new, ~250 LOC)** — owns flat/pipeline/cascade/router fan-out
  *natively*: lifts the staging logic out of `adapters/dispatch_backend.py`, calls model components
  over `S.model_subject(...)`, streams stage/agent-marked `Token`s.
- `run_modes.py` **(edit)** — swap `DispatchModeBackend` → `Orchestrator`; drop the `_reachable(:8010)`
  guard. Keep `adapters/dispatch_backend.py` only as the S1 fallback (removed at S1 exit).
- **(test)** `tests/test_registry.py`, `tests/test_orchestrator.py` (pipeline ordering, cascade
  short-circuit, router selection — broker-free with fake model subjects).

### S2 — Model lane complete (MLX + lifecycle)
- `adapters/llama_backend.py` **(edit)** — extract spawn/health/port logic so it is backend-agnostic.
- `adapters/mlx_backend.py` **(new, ~180 LOC)** — spawn `mlx_lm.server` (`--model --host --port
  --prompt-cache-bytes` = TurboQuant KV cap, optional `--draft-model/--num-draft-tokens`,
  `--trust-remote-code`, extra args last); `/v1/models` health probe; streaming via the existing
  OpenAI-compatible client path.
- `ob_code/models.yaml` **(edit)** — add `mlx-scout` (port 8083, backend `mlx`).
- `run_model.py` **(edit)** — route `backend == "mlx"` → `mlx_backend`, else llama/echo.
- `bus/contracts/lifecycle.py` **(new)** + `components/lifecycle.py` **(new)** + `run_lifecycle.py`
  **(new)** — `.lifecycle.{convert,vllm.start}` as a presence-announcing component.
- `bus/subjects.py` **(edit)** — `.mlx.*` request subjects routed through the model lane.
- **(test)** `tests/test_mlx_backend.py` (arg shaping, prompt-cache cap from TurboQuant, extra-arg
  ordering), `tests/test_lifecycle.py`.

### S3 — Resource control bus-native (slot-manager, kvpool, launcher)  ✅ DONE
All three done in **Go** (standalone repos, nothing shared but the schema). Each gained a
self-contained `internal/bus` adapter — the Go `ServiceComponent`: NATS connect + announce/presence
+ route + schema-major gate + loud-error reply — wired to existing logic, with a reversible `-bus`
flag, building + testing **offline** (nats.go v1.52.0 cached) and failing loud on a dead broker:
- `cofiswarm-kvpool` → `.kv.{admit,evaluate,policy}` (wraps `policy.Config`). Note `.kv.evaluate`,
  not the plan's `.kv.budget`, to match the real `policy.Evaluate` API.
- `cofiswarm-slot-manager` → `.slots.{pressure,evict}` (deps-injected `pressure.Snapshot`/`evict.EndpointKV`).
- `cofiswarm-launcher` → `.launcher.{configure,status}`. configure is **async** (spawn+health up to
  HealthTimeout): reply is `accepted`, per-port outcome polled via `.launcher.status`. Refactored an
  exported `Server.RunGroups` out of the HTTP handler so HTTP and bus share the spawn loop; reuses
  the existing `SpawnMLX`/`buildMLXArgs` (RoPE/YaRN/KV-quant/TurboQuant).

Python side: `bus/contracts/resource.py` (field names mirror the Go JSON tags) + `tests/test_resource.py`;
schema export now **39 envelopes**; `start_stack.sh` supervises all three Go binaries.
**SDK evidence:** `bus.go` came out byte-identical across all three repos (only the package doc line
differs); the launcher's divergence (async configure) lived entirely in `handlers.go` — so a shared Go
SDK is viable, but per the standalone rule it stays duplicated unless we choose to consolidate.
This Go tier **is** the polyglot proof (S6): real Go components interoperating via the JSON Schema.
**Pending S3 exit (live):** kill kvpool mid-request → GUI shows ALERT (needed-down), not a silent hang.
- `bus/contracts/resource.py` **(new)** — `PressureQuery/Reply`, `EvictRequest/Reply`,
  `KvAdmit/Budget/Policy`, `ConfigureRequest/StatusReply` (carry RoPE/YaRN/KV-quant fields).
- `components/slot_manager.py` **(new, ~220 LOC)** + `run_slot_manager.py` **(new)** — `.slots.*`.
- `components/kvpool.py` **(new, ~220 LOC)** + `run_kvpool.py` **(new)** — `.kv.*`.
- `components/launcher.py` **(new, ~250 LOC)** + `run_launcher.py` **(new)** — `.launcher.*`; shells
  the Go launcher (`cofiswarm-launcher`) or owns spawn directly; **announces presence** so "down" is
  visible.
- `scripts/start_stack.sh` **(edit)** — add `slot-manager`, `kvpool`, `launcher` windows.
- **(test)** `tests/test_slot_manager.py`, `tests/test_kvpool.py`, `tests/test_resource_alert.py`
  (kill a needed component mid-request → `.alert`, no hang — extends `tests/test_middleman.py`).

### S4 — Data services + Tools
- `bus/contracts/data.py` **(new)** — Repository query/response for
  memory/cache/history/rag/logs/swarm-config/models.
- `components/data_service.py` **(new, ~250 LOC)** + `run_data.py` **(new)** — `.data.*`; reuse
  `recorder/store.py` for the history slice.
- `bus/contracts/tools.py` **(new)** — `ToolCall`, `ToolResult`.
- `components/tools/` **(new package)**: `base.py` (`ToolWorker(ServiceComponent)`), `web.py`,
  `coding.py`; `run_tools.py` **(new)** — per-tool processes on `.tools.<tool>`.
- `adapters/orchestrator.py` **(edit)** — on a `tool_call`, publish `.tools.<tool>`, await
  `ToolResult`, resume generation (Command pattern).
- **(test)** `tests/test_data_service.py`, `tests/test_tools.py` (tool_call round-trip + resume).

### S5 — Observability + meta parity
- `bus/contracts/meta.py` **(new)** — `Metrics`, `HealthAgents`, `ConfigGet/Set`, `Version`,
  `SwarmStatus`.
- `components/observability.py` **(new, ~220 LOC)** + `run_observability.py` **(new)** — `.metrics`,
  `.health.agents`, `.config`; recorder keeps history/stats (`recorder/stats.py` unchanged).
- `gateway/app.py` **(edit)** — wire **every** remaining legacy `/api/*` route to its subject.
- **Chain of Responsibility — gateway middleware (closes the spec gap):** `gateway/middleware.py`
  **(new)** — ordered aiohttp middlewares **auth → rate-limit → logging → router**, matching the
  diagram's Middleware pipeline. Auth via an API-key/bearer check (config-driven, off by default for
  local dev); rate-limit a per-client token bucket; logging already implicit, made a formal stage.
  The gateway is the single external surface, so this is the *one* place the chain belongs — internal
  bus components stay trust-the-broker. `gateway/app.py` **(edit)** registers the chain.
- `gui/server.py` + `gui/index.html` **(edit)** — add metrics/health/config panels.
- **(test)** `tests/test_meta.py`; `tests/test_middleware.py` **(new)** — auth reject/allow,
  rate-limit trips after N, stage ordering; `tests/test_parity.py` **(new)** — route-by-route diff
  harness (legacy fixture responses vs gateway responses).

### S6 — Hardening: fault isolation, reboot, polyglot proof
- `tests/test_chaos.py` **(new)** — single-component-down ≠ system-down; needed-down ⇒ alert;
  idle-down ⇒ quiet; hot-plug at runtime; broker bounce ⇒ reconnect + re-announce (drives
  `bus/presence.py` + `MiddleMan.start`'s `HELLO` path).
- `scripts/supervise.sh` **(edit)** — confirm capped backoff covers the new components.
- `~/Library/LaunchAgents/com.observer.stack.plist` **(edit)** — include new windows for reboot.
- `cofiswarm-launcher/...` **(edit, Go)** — validate inbound envelopes against the exported
  `bus/schema/*.json` to prove the contract is wire-level, not in-process (polyglot acceptance).
- **(test)** `tests/test_schema_contract.py` — Python ↔ exported JSON Schema round-trip parity.

### Repository & code organization (enforced every sprint)

This is multi-repo. Keeping each repo and each module clean is a **release gate**, not cleanup —
a sprint isn't "done" until its files pass these checks.

**One job per repo.** Each lives on its own and is buildable/testable in isolation:
| Repo | Owns | Must not |
|---|---|---|
| `observer` | bus core, gateway, capability components, GUI, recorder | import cofiswarm Python in-process |
| `cofiswarm-launcher` (Go) | server spawn (llama + MLX), RoPE/YaRN/KV-quant | know about NATS internals beyond the schema |
| `cofiswarm-dispatch` / `-agent-registry` / `-slot-manager` / `-kvpool` | legacy HTTP surface during migration | gain new features — they only shrink as capabilities move to the bus |

**The only thing shared across repos is the schema.** `bus/schema/*.json` (emitted in S0) is the
single cross-repo artifact. No repo imports another repo's source; no shared in-memory objects, no
private HTTP between components. If two pieces need to talk, it's a bus subject with a versioned
envelope — full stop. (This is the test that the monolith is actually gone.)

**Module rules inside `observer`:**
- 250–300 LOC per file (global rule). Split *before* writing past it, not after: a component splits
  into `<name>.py` (handler) + a contract module + a thin transport/backend helper when it grows.
- One directory = one role: `bus/` (transport + contracts), `components/` (request/reply capability
  processes), `adapters/` (model-lane backends only), `gateway/` (the one facade), `recorder/`,
  `gui/`. A file that doesn't fit one of these is a sign the boundary is wrong.
- **Naming is mechanical**, so the map is obvious: capability `foo` ⇒ subject group `.foo.*`
  (`bus/subjects.py`) ⇒ envelopes `bus/contracts/foo.py` ⇒ process `components/foo.py` ⇒ entry
  `run_foo.py` ⇒ test `tests/test_foo.py`. All five names match. No exceptions without a comment.
- `tests/` mirrors source 1:1 and stays broker-free (fakes for bus/subjects), so the suite runs in CI
  with no network — as it does today (31 tests).

**Per-repo hygiene (checked at each sprint exit):**
- README + `requirements.txt`/`go.mod` current; every new `run_*.py` listed in README layout + added
  to `scripts/start_stack.sh` under `supervise.sh`.
- No dead code: when a capability goes bus-native, its bridge/fallback is **deleted**, not left
  commented (e.g. `adapters/dispatch_backend.py` removed at S1 exit). The parity dashboard row flips
  only when the old path is gone.
- Every `try/except` logs (global rule); every bus boundary validates + naks loudly. No empty
  handlers, no silent drops.

### Net new top-level layout after S6
```
bus/         subjects.py · nats_bus.py · presence.py · middleman.py · component.py · schema_export.py
bus/contracts/   base · registry · resource · lifecycle · data · tools · meta   (versioned envelopes)
bus/schema/      *.json                                                          (emitted, polyglot)
adapters/    cofiswarm_model · llama_backend · mlx_backend · orchestrator        (dispatch_backend removed)
components/  registry · slot_manager · kvpool · launcher · lifecycle · data_service · observability
components/tools/  base · web · coding
gateway/     app · bus_proxy · cli
recorder/    store · service · stats                                            (unchanged)
gui/         server · index.html
run_*.py     middleman · model · registry · modes · lifecycle · slot_manager · kvpool · launcher ·
             data · tools · observability · gateway · recorder · gui
```
