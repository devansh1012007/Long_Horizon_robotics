#!/usr/bin/env bash
# Full-stack production e2e: standalone Next.js server + engine sidecar.
# register -> login -> submit real job -> poll -> episodes/leaderboard/ops.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${E2E_PORT:-3111}"
EPORT="${E2E_ENGINE_PORT:-3032}"
ROOT="$(pwd)"
export DATABASE_URL="file:$ROOT/data/e2e-dashboard.db"
rm -f "$ROOT/data/e2e-dashboard.db"
rm -rf "$ROOT/data/e2e-engine"
bunx prisma db push --skip-generate >/dev/null 2>&1

# Dedicated engine instance (isolated port + data dir) so the test never
# depends on a leftover sidecar whose data dir may have been wiped.
ENGINE_HOST=127.0.0.1 ENGINE_PORT="$EPORT" ENGINE_DATA_DIR="$ROOT/data/e2e-engine" \
  python3 engine/index.py >"$ROOT/data/e2e-engine.log" 2>&1 &
EPID=$!
trap 'kill $PID $EPID 2>/dev/null || true' EXIT

echo "[e2e] waiting for engine on :$EPORT ..."
for i in $(seq 1 40); do
  if curl -sf "http://127.0.0.1:$EPORT/health" >/dev/null; then break; fi
  sleep 0.5
  [ "$i" = 40 ] && { echo "[e2e] engine failed to start"; tail -5 "$ROOT/data/e2e-engine.log"; exit 1; }
done

SERVER_JS="$ROOT/.next/standalone/server.js"
[ -f "$SERVER_JS" ] || SERVER_JS="$(find "$ROOT/.next/standalone" -name server.js 2>/dev/null | head -1)"
[ -n "$SERVER_JS" ] || { echo "[e2e] standalone server.js not found — run make build first"; exit 1; }

PORT=$PORT HOSTNAME=127.0.0.1 COOKIE_SECURE=0 ENGINE_URL="http://127.0.0.1:$EPORT" \
  node "$SERVER_JS" >"$ROOT/data/e2e-server.log" 2>&1 &
PID=$!

echo "[e2e] waiting for dashboard on :$PORT ..."
for i in $(seq 1 40); do
  if curl -sf "http://127.0.0.1:$PORT/" >/dev/null; then break; fi
  sleep 0.5
  [ "$i" = 40 ] && { echo "[e2e] dashboard failed to start"; tail -5 "$ROOT/data/e2e-server.log"; exit 1; }
done
echo "[e2e] dashboard is up"

B="http://127.0.0.1:$PORT"
echo "[e2e] register operator ..."
curl -sf -c "$ROOT/data/e2e-cookies.txt" -X POST "$B/api/auth/register" \
  -H 'Content-Type: application/json' \
  -d '{"name":"Ops Bot","email":"ops@example.com","password":"prod-grade-pass-1"}' >/dev/null
ME=$(curl -sf -b "$ROOT/data/e2e-cookies.txt" "$B/api/auth/me")
echo "[e2e] session: $ME"

echo "[e2e] unauthenticated access must be rejected ..."
CODE=$(curl -s -o /dev/null -w '%{http_code}' "$B/api/jobs")
[ "$CODE" = "401" ] || { echo "[e2e] expected 401, got $CODE"; exit 1; }
echo "[e2e] 401 confirmed"

echo "[e2e] submit job (RG + B5, horizon 3, 3 episodes, f_pose) ..."
SUB=$(curl -sf -b "$ROOT/data/e2e-cookies.txt" -X POST "$B/api/jobs" \
  -H 'Content-Type: application/json' \
  -d '{"systems":["RG","B5"],"horizons":[3],"episodes":3,"condition":"single","fault":"f_pose","base_seed":5100,"label":"prod-e2e"}')
JOB_ID=$(echo "$SUB" | python3 -c 'import sys,json;print(json.load(sys.stdin)["job_id"])')
echo "[e2e] job_id=$JOB_ID"

STATUS="queued"
for i in $(seq 1 120); do
  STATUS=$(curl -s -b "$ROOT/data/e2e-cookies.txt" "$B/api/jobs/$JOB_ID" | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])')
  [ "$STATUS" = "done" ] && break
  [ "$STATUS" = "failed" ] && { echo "[e2e] job FAILED"; exit 1; }
  sleep 0.5
done
[ "$STATUS" = "done" ] || { echo "[e2e] timeout"; exit 1; }
echo "[e2e] job done"

echo "[e2e] persisted aggregates:"
curl -s -b "$ROOT/data/e2e-cookies.txt" "$B/api/leaderboard" | python3 -c '
import sys, json
d = json.load(sys.stdin)
rows = d.get("entries") or d.get("rows") or []
for r in (rows if isinstance(rows, list) else [])[:6]:
    print("[e2e]", r)
'
curl -s -b "$ROOT/data/e2e-cookies.txt" "$B/api/ops" | python3 -c '
import sys, json
d = json.load(sys.stdin)
eng = d.get("engine", {})
print("[e2e] engine:", eng.get("status"), eng.get("version", ""))
print("[e2e] platform:", d.get("platform"))
'
curl -s -b "$ROOT/data/e2e-cookies.txt" "$B/api/episodes" | python3 -c '
import sys, json
d = json.load(sys.stdin)
total = d.get("total", 0)
rows = d.get("rows") or []
print("[e2e] episodes persisted:", total)
for r in rows[:3]:
    print("[e2e]", r.get("system"), r.get("epId"), r.get("outcome"),
          "extra_actions=", r.get("extraActions"))
assert total >= 1, "episode rows were not persisted to Prisma"
'
echo "[e2e] PASSED"
