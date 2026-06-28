#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

N="${1:-10}"
export INFER_HUB_URL="${INFER_HUB_URL:-http://127.0.0.1:8080}"
export INFER_HUB_API_KEY="${INFER_HUB_API_KEY:-}"

nice -n 10 .venv/bin/python scripts/load_test.py "$N"
