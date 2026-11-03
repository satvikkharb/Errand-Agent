#!/usr/bin/env bash
# Rebuild and restart the Errand backend (FastAPI) and frontend (Next.js) for local testing.
#
#   scripts/rebuild.sh                    both, on FE 3100 / BE 8010
#   scripts/rebuild.sh --be | --fe        only one of them
#   scripts/rebuild.sh --check            lint + typecheck the frontend first, abort on errors
#   scripts/rebuild.sh --fe-port=3000 --be-port=8000
#
# Env: STATE_BACKEND (default "memory": no database is touched), DEV_ENDPOINTS (default "true").
# It only ever stops processes that look like our own uvicorn / next dev. Anything else on the
# port (Docker, your own servers) is left alone and reported.

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BE_PORT="${BE_PORT:-8010}"
FE_PORT="${FE_PORT:-3100}"
DO_BE=1
DO_FE=1
CHECK=0
OUT="$ROOT/.rebuild"

usage() { sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

for a in "$@"; do
  case "$a" in
    --be) DO_FE=0 ;;
    --fe) DO_BE=0 ;;
    --check) CHECK=1 ;;
    --be-port=*) BE_PORT="${a#*=}" ;;
    --fe-port=*) FE_PORT="${a#*=}" ;;
    -h|--help) usage 0 ;;
    *) echo "unknown option: $a"; usage 2 ;;
  esac
done

mkdir -p "$OUT"

# A process is "ours" only if it is the right kind of server AND was started from inside this
# checkout (its working directory is under $ROOT). Your own servers, started from another folder
# or by Docker, never match, even if they are also uvicorn or next.
owned() { # PID
  local cwd
  cwd="$(lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
  [[ -n "$cwd" && "$cwd/" == "$ROOT/"* ]]
}

# stop_ours PORT PATTERN FLAG: stop our own server on PORT; refuse to touch anything else.
stop_ours() {
  local port="$1" pattern="$2" flag="$3" pids pid cmd
  pids="$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null)"
  [ -z "$pids" ] && return 0
  for pid in $pids; do
    cmd="$(ps -o command= -p "$pid" 2>/dev/null)"
    if [[ "$cmd" =~ $pattern ]] && owned "$pid"; then
      kill "$pid" 2>/dev/null
    else
      echo "✗ port $port is in use by a program this checkout didn't start, so I left it alone:"
      echo "    ${cmd:0:100} (pid $pid)"
      echo "  Use a free port with --$flag-port=N"
      return 1
    fi
  done
  for _ in $(seq 1 25); do
    [ -z "$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null)" ] && return 0
    sleep 0.2
  done
  echo "✗ the old server on port $port would not stop"
  return 1
}

wait_for() { # URL LABEL SECONDS
  local url="$1" label="$2" secs="${3:-60}"
  for _ in $(seq 1 "$secs"); do
    curl -sf -m 3 "$url" >/dev/null 2>&1 && return 0
    sleep 1
  done
  echo "✗ $label did not come up within ${secs}s"
  return 1
}

STATUS=0

# ------------------------------------------------------------------ backend
if [ "$DO_BE" = 1 ]; then
  echo "▸ backend: restarting on :$BE_PORT"
  stop_ours "$BE_PORT" "uvicorn|python" be || exit 1

  # The key lives in the main checkout's gitignored backend/.env. A worktree doesn't have its own.
  if [ ! -e "$ROOT/backend/.env" ]; then
    COMMON="$(cd "$ROOT" && git rev-parse --git-common-dir 2>/dev/null)"
    MAIN="$(cd "$ROOT" && cd "$COMMON/.." 2>/dev/null && pwd)"
    if [ -n "$MAIN" ] && [ -f "$MAIN/backend/.env" ] && [ "$MAIN" != "$ROOT" ]; then
      ln -s "$MAIN/backend/.env" "$ROOT/backend/.env"
      echo "  linked backend/.env from the main checkout"
    else
      echo "  ! no backend/.env found: the agent needs OPENAI_API_KEY (copy backend/.env.example)"
    fi
  fi

  (
    cd "$ROOT/backend" || exit 1
    STATE_BACKEND="${STATE_BACKEND:-memory}" DEV_ENDPOINTS="${DEV_ENDPOINTS:-true}" \
      nohup uv run uvicorn app.main:app --port "$BE_PORT" >"$OUT/backend.log" 2>&1 </dev/null &
  )
  if wait_for "http://localhost:$BE_PORT/health" "backend" 90; then
    echo "  ✓ backend up"
  else
    echo "  see $OUT/backend.log"; tail -5 "$OUT/backend.log"; STATUS=1
  fi
fi

# ----------------------------------------------------------------- frontend
if [ "$DO_FE" = 1 ]; then
  echo "▸ frontend: rebuilding on :$FE_PORT"
  stop_ours "$FE_PORT" "next|node" fe || exit 1

  cd "$ROOT/frontend" || exit 1
  if [ ! -d node_modules ] || [ package-lock.json -nt node_modules/.package-lock.json ]; then
    echo "  installing dependencies"
    npm install --no-audit --no-fund >"$OUT/npm-install.log" 2>&1 || { echo "✗ npm install failed, see $OUT/npm-install.log"; exit 1; }
  fi

  if [ "$CHECK" = 1 ]; then
    echo "  checking (lint + typecheck)"
    npm run lint --silent || { echo "✗ lint failed, not restarting the frontend"; exit 1; }
    npx tsc --noEmit || { echo "✗ type errors, not restarting the frontend"; exit 1; }
  fi

  DIST=".next-$FE_PORT"
  # a clean build, so a stale cache can't hide a problem. The old server may still be flushing files
  # for a moment after it stops, so retry.
  for _ in 1 2 3 4 5; do rm -rf "$DIST" 2>/dev/null && break; sleep 1; done
  NEXT_DIST_DIR="$DIST" BACKEND_URL="http://localhost:$BE_PORT" \
    nohup npm run dev -- -p "$FE_PORT" >"$OUT/frontend.log" 2>&1 </dev/null &

  if wait_for "http://localhost:$FE_PORT/login" "frontend" 90; then
    echo "  ✓ frontend up"
  else
    echo "  see $OUT/frontend.log"; tail -5 "$OUT/frontend.log"; STATUS=1
  fi
fi

# ------------------------------------------------------------------ summary
echo
if [ "$DO_FE" = 1 ] && [ "$DO_BE" = 1 ] && [ "$STATUS" = 0 ]; then
  if curl -sf -m 5 "http://localhost:$FE_PORT/api/be/health" >/dev/null 2>&1; then
    echo "✓ frontend → backend connected (via the /api/be proxy)"
  else
    echo "✗ frontend is up but cannot reach the backend on :$BE_PORT"; STATUS=1
  fi
fi
[ "$DO_FE" = 1 ] && echo "  Frontend  http://localhost:$FE_PORT"
[ "$DO_BE" = 1 ] && echo "  Backend   http://localhost:$BE_PORT   (state: ${STATE_BACKEND:-memory})"
echo "  Logs      $OUT/{backend,frontend}.log"
exit "$STATUS"
