#!/bin/bash
# Supervise one service: (re)start it whenever it exits, with capped exponential backoff.
# Streams output to both the tmux pane and .run/<name>.log. Used by start_stack.sh so each
# service is self-healing without taking down the rest of the stack.
#
#   supervise.sh <name> <command> [args...]
set -u

NAME="$1"; shift
DIR="/Users/caribou/observer"
LOG="$DIR/.run/${NAME}.log"
cd "$DIR" || exit 1

backoff=1
while true; do
  echo "[supervise $(date '+%H:%M:%S')] starting '$NAME': $*" | tee -a "$LOG"
  start=$(date +%s)
  "$@" 2>&1 | tee -a "$LOG"
  code=${PIPESTATUS[0]}
  ran=$(( $(date +%s) - start ))
  echo "[supervise $(date '+%H:%M:%S')] '$NAME' exited code=$code after ${ran}s" | tee -a "$LOG"

  # Healthy long run resets backoff; rapid crashes back off up to 30s.
  if [ "$ran" -ge 30 ]; then
    backoff=1
  else
    backoff=$(( backoff * 2 )); [ "$backoff" -gt 30 ] && backoff=30
  fi
  echo "[supervise] restarting '$NAME' in ${backoff}s" | tee -a "$LOG"
  sleep "$backoff"
done
