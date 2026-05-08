#!/usr/bin/env bash
set -euo pipefail
BASE_URL="${TEAMRAG_URL:-http://localhost:8000}"
curl -sf "$BASE_URL/health" | grep '"status":"ok"'
curl -sf -X POST "$BASE_URL/query" \
  -H "Content-Type: application/json" \
  -d '{"query":"test","top_k":5}' | grep '"chunks":\[\]'
echo "Smoke test passed."
