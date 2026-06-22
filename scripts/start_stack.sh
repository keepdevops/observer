#!/bin/bash
# Bring up the observer stack in a detached tmux session. Idempotent: if the session
# already exists, it does nothing. Each window runs under supervise.sh, so a crashed
# service is restarted automatically without affecting the rest of the stack.
# Invoked by the launchd agent at login/reboot, and usable by hand:
#   bash scripts/start_stack.sh
set -u

SESSION="observer"
DIR="/Users/caribou/observer"
PY="/Users/caribou/miniforge3/envs/mlx-env/bin/python3"
NATS="/opt/homebrew/bin/nats-server"
TMUX="/opt/homebrew/bin/tmux"
SUP="bash $DIR/scripts/supervise.sh"

# cofiswarm orchestration backend (dispatch + the four mode responders that fan out
# to the live model servers). Ports: dispatch 8010; flat 8025 (8021 is held by a stale
# launchd socket), pipeline 8022, cascade 8023, router 8024.
COFI="/Users/caribou/cofiswarm/repos"
VARLIB="$HOME/.cofiswarm"
SWARM_CONFIG="/Users/caribou/cofiswarmdev/swarm-config.json"
DISPATCH_BIN="$COFI/cofiswarm-dispatch/bin/cofiswarm-dispatch"
FLAT_PORT=8025
declare -A MODE_PORT=( [flat]=$FLAT_PORT [pipeline]=8022 [cascade]=8023 [router]=8024 )

cd "$DIR" || { echo "cannot cd to $DIR"; exit 1; }
mkdir -p .run "$VARLIB/dispatch/sessions" "$VARLIB/dispatch/history" "$VARLIB/modes"

# Generate a mode responder config if it's missing (reboot-safe; preserves edits).
ensure_mode_cfg() {
  local mode="$1" port="$2" path="$VARLIB/modes/mode-$mode.yaml"
  [ -f "$path" ] && return 0
  cat > "$path" <<YAML
mode: mode-$mode
listen: ":$port"
dispatch_url: http://127.0.0.1:8010
slot_manager_url: http://127.0.0.1:8013
kvpool_url: http://127.0.0.1:8014
agent_registry_url: http://127.0.0.1:8012
swarm_config_path: $SWARM_CONFIG
infer_host: 127.0.0.1
default_max_tokens: 96
YAML
}

if "$TMUX" has-session -t "$SESSION" 2>/dev/null; then
  echo "observer: session already running — nothing to do"
  exit 0
fi

# window 0: the broker (own it; supervised so a crash self-heals — clients auto-reconnect)
"$TMUX" new-session -d -s "$SESSION" -n nats -c "$DIR" "$SUP nats $NATS -p 4222"
# wait for the broker to accept connections before starting clients
for _ in $(seq 1 20); do nc -z 127.0.0.1 4222 && break; sleep 0.5; done

"$TMUX" new-window -t "$SESSION" -n middleman -c "$DIR" "$SUP middleman $PY run_middleman.py"
sleep 1
"$TMUX" new-window -t "$SESSION" -n echo-fast  -c "$DIR" "$SUP echo-fast $PY run_model.py --name echo-fast"
"$TMUX" new-window -t "$SESSION" -n registry   -c "$DIR" "$SUP registry $PY run_registry.py"
"$TMUX" new-window -t "$SESSION" -n lifecycle  -c "$DIR" "$SUP lifecycle $PY run_lifecycle.py"
"$TMUX" new-window -t "$SESSION" -n data       -c "$DIR" "$SUP data $PY run_data.py"
"$TMUX" new-window -t "$SESSION" -n tools      -c "$DIR" "$SUP tools $PY run_tools.py"
"$TMUX" new-window -t "$SESSION" -n observ     -c "$DIR" "$SUP observ $PY run_observability.py"
"$TMUX" new-window -t "$SESSION" -n recorder   -c "$DIR" "$SUP recorder $PY run_recorder.py"

# resource tier (Go, standalone repos) — bus-native via -bus; each announces presence so
# "down" is visible. Built separately: (cd <repo> && go build -o bin/<repo> ./cmd/<repo>).
KVPOOL_BIN="$COFI/cofiswarm-kvpool/bin/cofiswarm-kvpool"
SLOTMGR_BIN="$COFI/cofiswarm-slot-manager/bin/cofiswarm-slot-manager"
LAUNCHER_BIN="$COFI/cofiswarm-launcher/bin/cofiswarm-configure"
[ -x "$KVPOOL_BIN" ] && "$TMUX" new-window -t "$SESSION" -n kvpool \
  -c "$DIR" "$SUP kvpool $KVPOOL_BIN -bus" \
  || echo "observer: WARN kvpool binary missing ($KVPOOL_BIN) — skipping" >&2
[ -x "$SLOTMGR_BIN" ] && "$TMUX" new-window -t "$SESSION" -n slot-manager \
  -c "$DIR" "$SUP slot-manager $SLOTMGR_BIN -bus" \
  || echo "observer: WARN slot-manager binary missing ($SLOTMGR_BIN) — skipping" >&2
[ -x "$LAUNCHER_BIN" ] && "$TMUX" new-window -t "$SESSION" -n launcher \
  -c "$DIR" "$SUP launcher $LAUNCHER_BIN -bus" \
  || echo "observer: WARN launcher binary missing ($LAUNCHER_BIN) — skipping" >&2
"$TMUX" new-window -t "$SESSION" -n gui        -c "$DIR" "$SUP gui $PY run_gui.py --port 8099"
"$TMUX" new-window -t "$SESSION" -n gateway    -c "$DIR" "$SUP gateway $PY run_gateway.py --port 8100"
sleep 1
# bridge all live cofiswarm agents (skips any whose backing server is down)
"$TMUX" new-window -t "$SESSION" -n cofiswarm  -c "$DIR" "$SUP cofiswarm $PY run_cofiswarm.py"

# cofiswarm orchestration backend: dispatch + the four mode responders. These must be up
# before the 'modes' window, since run_modes.py exits if dispatch (:8010) is unreachable.
if [ -x "$DISPATCH_BIN" ]; then
  "$TMUX" new-window -t "$SESSION" -n dispatch -c "$DIR" \
    "$SUP dispatch env COFISWARM_MODE_FLAT_PORT=$FLAT_PORT $DISPATCH_BIN -listen :8010 -state $VARLIB/dispatch"
  for mode in flat pipeline cascade router; do
    bin="$COFI/cofiswarm-mode-$mode/bin/cofiswarm-mode-$mode"
    if [ -x "$bin" ]; then
      ensure_mode_cfg "$mode" "${MODE_PORT[$mode]}"
      "$TMUX" new-window -t "$SESSION" -n "mode-$mode" -c "$DIR" \
        "$SUP mode-$mode $bin -config $VARLIB/modes/mode-$mode.yaml"
    else
      echo "observer: WARN mode-$mode binary missing ($bin) — skipping" >&2
    fi
  done
  # wait for dispatch to accept connections before bridging the modes
  for _ in $(seq 1 20); do nc -z 127.0.0.1 8010 && break; sleep 0.5; done
else
  echo "observer: WARN dispatch binary missing ($DISPATCH_BIN) — modes will run degraded" >&2
fi

# bridge cofiswarm orchestration modes (flat/pipeline/cascade/router) via dispatch :8010
"$TMUX" new-window -t "$SESSION" -n modes      -c "$DIR" "$SUP modes $PY run_modes.py"

echo "observer: stack started (tmux session '$SESSION', all windows supervised)"
