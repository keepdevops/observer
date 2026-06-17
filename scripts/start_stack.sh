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

cd "$DIR" || { echo "cannot cd to $DIR"; exit 1; }
mkdir -p .run

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
"$TMUX" new-window -t "$SESSION" -n recorder   -c "$DIR" "$SUP recorder $PY run_recorder.py"
"$TMUX" new-window -t "$SESSION" -n gui        -c "$DIR" "$SUP gui $PY run_gui.py --port 8099"
sleep 1
# bridge all live cofiswarm agents (skips any whose backing server is down)
"$TMUX" new-window -t "$SESSION" -n cofiswarm  -c "$DIR" "$SUP cofiswarm $PY run_cofiswarm.py"
# bridge cofiswarm orchestration modes (flat/pipeline/cascade/router) via dispatch :8010
"$TMUX" new-window -t "$SESSION" -n modes      -c "$DIR" "$SUP modes $PY run_modes.py"

echo "observer: stack started (tmux session '$SESSION', all windows supervised)"
