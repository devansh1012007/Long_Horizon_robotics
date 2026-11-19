#!/usr/bin/env bash
# End-to-end engine smoke test: boots the engine service standalone,
# submits a real simulation job through the HTTP API, and polls it to
# completion. No dashboard required — proves the research core + service
# layer work as deployed.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${SMOKE_PORT:-3131}"
ENGINE_HOST=127.0.0.1 ENGINE_PORT="$PORT" ENGINE_DATA_DIR="$(pwd)/data/smoke-engine" \
  python3 engine/index.py &
PID=$!
trap 'kill $PID 2>/dev/null || true' EXIT

echo "[smoke] waiting for engine on :$PORT ..."
for i in $(seq 1 40); do
  if curl -sf "http://127.0.0.1:$PORT/health" >/dev/null; then break; fi
  sleep 0.5
  [ "$i" = 40 ] && { echo "[smoke] engine failed to start"; exit 1; }
done
echo "[smoke] engine healthy: $(curl -s "http://127.0.0.1:$PORT/health" | head -c 160) ..."

echo "[smoke] submitting job (RG + B2 x horizon 2 x 2 episodes, f_slip) ..."
JOB=$(curl -s -X POST "http://127.0.0.1:$PORT/jobs" -H 'Content-Type: application/json' -d '{
  "user_id": "smoke",
  "spec": {"systems": ["RG", "B2"], "horizons": [2], "episodes": 2,
           "condition": "single", "fault": "f_slip", "base_seed": 9000,
           "label": "smoke-test"}}')
JOB_ID=$(echo "$JOB" | python3 -c 'import sys,json;print(json.load(sys.stdin)["job_id"])')
echo "[smoke] job_id=$JOB_ID"

STATUS="queued"
for i in $(seq 1 120); do
  STATUS=$(curl -s "http://127.0.0.1:$PORT/jobs/$JOB_ID" | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])')
  [ "$STATUS" = "done" ] && break
  [ "$STATUS" = "failed" ] && { echo "[smoke] job FAILED"; exit 1; }
  sleep 0.5
done
[ "$STATUS" = "done" ] || { echo "[smoke] timeout waiting for job"; exit 1; }

curl -s "http://127.0.0.1:$PORT/jobs/$JOB_ID" | python3 -c '
import sys, json
j = json.load(sys.stdin)
print("[smoke] status:", j["status"], "progress:", j["progress"], "/", j["total"])
for sys_id, s in (j["summary"] or {}).items():
    print("[smoke]", sys_id,
          "n=", s.get("n"),
          "success=", s.get("success_rate"),
          "CI=[", s.get("ci_lo"), ",", s.get("ci_hi"), "]",
          "diag_acc=", s.get("diag_accuracy"))
for st in (j["stats"] or []):
    print("[smoke] RG vs", st.get("vs"),
          "delta=", st.get("delta_success_mean"),
          "CI=[", st.get("delta_ci_lo"), ",", st.get("delta_ci_hi"), "]",
          "mcnemar_p=", st.get("mcnemar_p"))
'
echo "[smoke] PASSED"
