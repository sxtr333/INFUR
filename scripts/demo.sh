#!/usr/bin/env bash
# One-shot demo: health check + submit job + wait for result.
set -euo pipefail
cd "$(dirname "$0")/.."

BASE="${INFER_HUB_URL:-http://127.0.0.1:8080}"
KEY="${INFER_HUB_API_KEY:-}"
HDR=()
if [[ -n "${KEY}" ]]; then
  HDR=(-H "X-API-Key: ${KEY}")
fi

echo "==> Health"
curl -sf "${BASE}/health" | python3 -m json.tool

echo
echo "==> Submit job"
JOB=$(curl -sf -X POST "${BASE}/v1/jobs" \
  "${HDR[@]}" \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"В двух предложениях: что делает job queue с приоритетами?","priority":9,"max_tokens":120}')
echo "${JOB}" | python3 -m json.tool
ID=$(echo "${JOB}" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

echo
echo "==> Wait for worker (GPU) ..."
for _ in $(seq 1 90); do
  STATUS=$(curl -sf "${BASE}/v1/jobs/${ID}" "${HDR[@]}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['status']); print(d.get('result') or d.get('error') or '', end='')")
  STATE=$(echo "${STATUS}" | head -1)
  if [[ "${STATE}" == "done" ]]; then
    echo
    echo "--- result ---"
    echo "${STATUS}" | tail -n +2
    exit 0
  fi
  if [[ "${STATE}" == "failed" ]]; then
    echo "FAILED:"
    echo "${STATUS}" | tail -n +2
    exit 1
  fi
  sleep 2
done
echo "timeout waiting for job ${ID}"
exit 1
