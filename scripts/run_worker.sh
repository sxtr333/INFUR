#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

export CPU_LIMIT_PCT="${CPU_LIMIT_PCT:-70}"
if (( $(python3 - <<'PY'
import os
print(1 if float(os.getenv("CPU_LIMIT_PCT", "70")) > 75 else 0)
PY
) )); then
  export CPU_LIMIT_PCT=75
fi

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

touch data/worker.alive
trap 'rm -f data/worker.alive' EXIT

exec .venv/bin/python -m infer_hub.worker.runner
