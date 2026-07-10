#!/usr/bin/env bash
#
# Run Gater locally under a URL base path, to exercise base-path support
# (and, if you load an OMERO-backed dataset, the live OMERO connection).
#
# Usage:
#   ./run_local.sh
#   GATER_BASE_PATH=/user/alice PORT=8000 ./run_local.sh
#   OMERO_SESSION=<uuid> ./run_local.sh          # join an existing OMERO session
#
# Then open:  http://localhost:<PORT><GATER_BASE_PATH>/
# (plain http://localhost:<PORT>/ also works locally — the middleware only
#  *strips* the prefix when present, it doesn't require it.)
#
# base-path support needs NO proxy: PrefixMiddleware strips GATER_BASE_PATH
# itself, so requests to /user/test/... route correctly on their own.
set -euo pipefail

# --- config (all overridable via env) ----------------------------------------
export GATER_BASE_PATH="${GATER_BASE_PATH:-/user/test}"
PORT="${PORT:-8000}"

# OMERO connection for the live-tile path. Defaults match the repo's
# docker-compose OMERO stack (root/omero on localhost:4064). Only used if you
# actually open an OMERO-backed dataset — base-path testing with the existing
# local datasets (e.g. kidney) needs no OMERO at all.
export OMERO_HOST="${OMERO_HOST:-localhost}"
export OMERO_PORT="${OMERO_PORT:-4064}"
export OMERO_USER="${OMERO_USER:-root}"
export OMERO_PASSWORD="${OMERO_PASSWORD:-omero}"
# If set, Gater joins this existing OMERO session instead of user/pass login.
[ -n "${OMERO_SESSION:-}" ] && export OMERO_SESSION

# --- pick a Python interpreter -----------------------------------------------
DEFAULT_CONDA_PY="/opt/anaconda3/envs/minerva_analysis/bin/python"
if [ -n "${PYTHON:-}" ]; then
  PY="$PYTHON"
elif [ -x "$DEFAULT_CONDA_PY" ]; then
  PY="$DEFAULT_CONDA_PY"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
else
  PY="python"
fi

# --- run from the gater/ dir (where minerva_analysis/ and run.py live) --------
cd "$(dirname "$0")"

echo "──────────────────────────────────────────────────────────────"
echo " Gater local run"
echo "   python     : $PY"
echo "   base path  : $GATER_BASE_PATH"
echo "   OMERO      : $OMERO_USER@$OMERO_HOST:$OMERO_PORT ${OMERO_SESSION:+(session override set)}"
echo "   OPEN URL   : http://localhost:$PORT$GATER_BASE_PATH/"
echo "   (validate with: PORT=$PORT GATER_BASE_PATH=$GATER_BASE_PATH ./smoke_base_path.sh)"
echo "──────────────────────────────────────────────────────────────"

# run.py takes the port as argv[1]; omitting argv[2] leaves is_docker=False.
exec "$PY" run.py "$PORT"
