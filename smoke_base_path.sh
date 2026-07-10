#!/usr/bin/env bash
#
# Smoke-test that Gater is correctly serving under GATER_BASE_PATH.
# Run this in a SECOND terminal while ./run_local.sh is running.
#
# Usage:
#   ./smoke_base_path.sh
#   GATER_BASE_PATH=/user/alice PORT=8000 ./smoke_base_path.sh
set -uo pipefail

BASE="${GATER_BASE_PATH:-/user/test}"
PORT="${PORT:-8000}"
ROOT="http://localhost:$PORT"
fail=0

pass() { printf "  \033[32mPASS\033[0m %s\n" "$1"; }
bad()  { printf "  \033[31mFAIL\033[0m %s\n" "$1"; fail=1; }
code() { curl -s -o /dev/null -w '%{http_code}' "$1"; }

echo "Smoke-testing $ROOT$BASE ..."

# 1. Index under the prefix returns 200.
[ "$(code "$ROOT$BASE/")" = "200" ] \
  && pass "GET $BASE/ -> 200" \
  || bad  "GET $BASE/ did not return 200"

# 2. The served HTML injects the base path into JS and prefixes its assets.
html="$(curl -s "$ROOT$BASE/")"
echo "$html" | grep -q "window.GATER_BASE_PATH = \"$BASE\"" \
  && pass "window.GATER_BASE_PATH injected as \"$BASE\"" \
  || bad  "window.GATER_BASE_PATH not injected"
echo "$html" | grep -q "$BASE/client/" \
  && pass "static assets prefixed with $BASE/client/" \
  || bad  "static assets NOT prefixed with the base path"

# 3. The base-path shim itself is reachable under the prefix.
[ "$(code "$ROOT$BASE/client/src/js/services/basePath.js")" = "200" ] \
  && pass "basePath.js served under the prefix" \
  || bad  "basePath.js 404 under the prefix"

# 4. An API endpoint routes correctly under the prefix.
[ "$(code "$ROOT$BASE/config")" = "200" ] \
  && pass "GET $BASE/config -> 200" \
  || bad  "GET $BASE/config did not return 200"

echo ""
if [ "$fail" = "0" ]; then
  echo "All base-path smoke checks passed."
else
  echo "Some checks FAILED — see above."
  exit 1
fi
