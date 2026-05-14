#!/usr/bin/env bash
set -euo pipefail
BASE_URL="${TEAMRAG_URL:-http://localhost:8000}"
curl -sf --max-time 10 --connect-timeout 5 "$BASE_URL/health" | grep '"status":"ok"'
curl -sf --max-time 10 --connect-timeout 5 -X POST "$BASE_URL/query" \
  -H "Content-Type: application/json" \
  -d '{"query":"test","top_k":5}' | grep '"chunks"'
curl -sf --max-time 10 --connect-timeout 5 -X POST "$BASE_URL/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"test"}]}' | grep '"object":"chat.completion"'
echo "Smoke test passed."
