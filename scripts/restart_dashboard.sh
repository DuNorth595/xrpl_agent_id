#!/usr/bin/env bash
# restart_dashboard.sh
# ---------------------------------------------------------------------------
# Kill any process bound to the XRPL Agent ID dashboard port (default 8768),
# then start a fresh instance with the current source tree. Prints the new
# PID and the access URL.
#
# Usage:
#   scripts/restart_dashboard.sh                # port 8768
#   scripts/restart_dashboard.sh 8769           # different port
#   PORT=8769 scripts/restart_dashboard.sh
#
# The script intentionally does NOT background itself with nohup/disown; it
# launches the server as a foreground job and exits, leaving the server as a
# detached child of init (no terminal hold). The PID is printed so you can
# kill it later with `kill <pid>` or `lsof -ti :<port> | xargs kill`.
# ---------------------------------------------------------------------------
set -euo pipefail

PORT="${1:-${PORT:-8768}}"
PROJECT_DIR="${XRPL_AGENT_ID_DIR:-$HOME/Desktop/LIFE_MEMORY/PROJECTS/XRPL_AGENT_ID}"
PYTHON="${PYTHON:-/usr/bin/python3}"
LOG_FILE="${LOG_FILE:-/tmp/xrpl_dashboard_${PORT}.log}"

if [[ ! -d "$PROJECT_DIR" ]]; then
  echo "ERROR: project directory not found: $PROJECT_DIR" >&2
  echo "       set XRPL_AGENT_ID_DIR or pass it via env" >&2
  exit 2
fi

echo "[restart] target port:      $PORT"
echo "[restart] project dir:      $PROJECT_DIR"
echo "[restart] python:           $PYTHON"
echo "[restart] log file:         $LOG_FILE"

# 1. Kill anything already listening on the target port.
EXISTING_PIDS="$(lsof -ti :"$PORT" 2>/dev/null || true)"
if [[ -n "$EXISTING_PIDS" ]]; then
  echo "[restart] killing existing PIDs on :$PORT -> $EXISTING_PIDS"
  # shellcheck disable=SC2086
  kill $EXISTING_PIDS 2>/dev/null || true
  # Give the kernel a beat to release the socket.
  for _ in 1 2 3 4 5; do
    sleep 0.3
    if ! lsof -ti :"$PORT" >/dev/null 2>&1; then
      break
    fi
  done
  if lsof -ti :"$PORT" >/dev/null 2>&1; then
    echo "[restart] ERROR: port $PORT still busy after SIGTERM" >&2
    exit 3
  fi
else
  echo "[restart] port :$PORT was free"
fi

# 2. Start fresh server detached from this terminal.
cd "$PROJECT_DIR"
echo "[restart] starting dashboard..."
# Background + redirect. No `setsid` on macOS by default; closing this
# script's stdin/out is enough to detach the child from the controlling tty.
"$PYTHON" -m xrpl_agent_id.dashboard.server \
      --port "$PORT" \
      >"$LOG_FILE" 2>&1 < /dev/null &
NEW_PID=$!
disown 2>/dev/null || true

# 3. Wait for the port to come up (max ~6 seconds).
READY=0
for _ in $(seq 1 20); do
  sleep 0.3
  if curl -sf -o /dev/null "http://127.0.0.1:$PORT/api/health"; then
    READY=1
    break
  fi
done

if [[ "$READY" -ne 1 ]]; then
  echo "[restart] ERROR: server did not become healthy on :$PORT" >&2
  echo "[restart] last 40 log lines:" >&2
  tail -n 40 "$LOG_FILE" >&2 || true
  exit 4
fi

echo "[restart] OK   PID=$NEW_PID  URL=http://127.0.0.1:$PORT"
echo "[restart] log tail:"
tail -n 3 "$LOG_FILE" | sed 's/^/         /'
