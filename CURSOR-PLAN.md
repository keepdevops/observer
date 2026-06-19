# CURSOR-PLAN — Coordinator → Observer conversion

Status of decomposing the legacy monolith **coordinator** (C++, `cofiswarm-proxy/cpp_core`,
archived reference) into **Observer (NATS bus middleman) as the coordination spine** plus standalone
HTTP components in the `cofiswarm-*` split repos.

## Architecture (target, mostly realized)
```
GUI / Observer ──► middleman (NATS bus) ──► mode components ──► dispatch :8010
                                                                  └► mode responders (flat 8025 / pipeline 8022 / cascade 8023 / router 8024)
                                                                        └► model servers (llama-server; MLX = WIP)
  agent-registry :8012   slot-manager :8013   kvpool :8014   launcher/configure :8017
```

## Conversion dashboard (legacy coordinator surface → go-forward home)
| Capability | New home | Status | Live |
|---|---|---|---|
| `/api/architect[/stream]` orchestration | dispatch | ✅ done, verified | :8010 |
| pipeline/cascade/router fan-out | mode responders (mode-sdk) | ✅ real multi-agent fan-out verified | ✅ |
| `/api/modes`, `/modes/active`, `/modes/{n}/agents` | agent-registry | ✅ done (drives Observer topology) | :8012 |
| `/api/agents` registry | agent-registry | ✅ done | :8012 |
| `/api/swarm-config,models,memory,cache*,history*,logs,rag/health` | dispatch (compat) | ✅ ported | :8010 |
| `/api/pressure[/evict]`, slot eviction | slot-manager | ✅ done | :8013 (down) |
| KV admission/budget/policy | kvpool (new) | ✅ done | :8014 |
| `/api/configure[/status]` server spawn | launcher/configure | ✅ done; now emits RoPE/YaRN/KV-quant | :8017 (down) |
| bus presence/alerts, GUI, `/roles`, `/modes`, topology | Observer middleman + GUI | ✅ done | :8099 |
| `/api/swarm/status`, `/api/version` | Observer (derived from bus roster) | ✅ done | :8099 |

## Not yet converted (gaps)
1. **MLX lane (biggest gap, IN PROGRESS):** legacy `/api/mlx/{agents,health,modes,stream,submit,
   session/clear,pressure}`. Launcher `continue`d past non-llama backends, so MLX servers
   (`mlx-scout`) were never spawned — which also blocks a real **TurboQuant** run. See workstream below.
2. **Model lifecycle:** `/api/models/convert`, `/api/inference/vllm/start` — no go-forward owner.
3. **Meta/status:** ✅ `/api/version` + `/api/swarm/status` now served by the Observer (derived from
   the bus roster). Remaining: `/api/metrics` aggregation, `/api/health/agents`, `/api/v1/config`.
4. `/api/orchestrate[/stream]` — legacy alias of architect/stream; superseded.

## Active workstream: MLX spawner (unblocks MLX lane + TurboQuant)
**Repo:** `cofiswarm-launcher/internal/configure`.
- `ports.go::BuildPortGroups` — stop skipping `mlx`; build MLX port-groups too (still skip other
  backends like `docker`). Tag the group's backend so the dispatcher can route.
- NEW `spawn_mlx.go`:
  - `mlxPython()` — `MATRIX_MLX_PYTHON` env, else `~/miniforge3/envs/mlx-env/bin/python`, else PATH.
  - `buildMLXArgs(g)` → `-m mlx_lm.server --model <path> --host 127.0.0.1 --port <p>` plus
    `--max-tokens`, `--prompt-cache-bytes` (TurboQuant KV cap), `--draft-model`/`--num-draft-tokens`,
    `--trust-remote-code`, and `ExtraArgs` last. (mlx_lm 0.31.3 has **no** server `--kv-bits`; KV is
    capped via `--prompt-cache-bytes`, quant is the 4bit model — the honest TurboQuant mapping.)
  - `SpawnMLX(g, logDir)` — mirrors `SpawnLlama` (killPort, setpgid, log file).
- `spawn.go::killPort` — allow `python` as an owner name so MLX servers can be replaced.
- `server.go` spawn loop — route `g.Backend == "mlx"` → `SpawnMLX`, else `SpawnLlama`. Health probe
  already accepts `/v1/models` (mlx_lm.server exposes that, not `/health`).
- Tests in `spawn_mlx_test.go`: arg shaping, prompt-cache cap from TurboQuant, extra_args ordering.

**Verify:** `go test ./...`; then live — configure `mlx-scout` (8083) and confirm `mlx_lm.server`
spawns, `/v1/models` is healthy, and a prompt returns; SCALE-6 turbo audit already passes on config.

**Out of scope (follow-on):** PolarQuant+QJL algorithm; `/api/mlx/*` HTTP compat surface on dispatch;
model-convert / vLLM lifecycle; `/api/swarm/status` aggregator.
