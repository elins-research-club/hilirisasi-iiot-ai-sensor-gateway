#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

bash deployment/iiotgw/build-release.sh
SHA="$(git rev-parse HEAD)"
RELEASE_ID="$(printf '%.12s' "$SHA")"
STAGE="$ROOT/.release-stage/$RELEASE_ID"
REMOTE_BASE="/home/iiotgw/apps/iiot-ai-sensor-gateway"
REMOTE_RELEASE="$REMOTE_BASE/releases/$RELEASE_ID"

echo "==> Upload release $RELEASE_ID"
tar -C "$STAGE" -czf - . | ssh -o BatchMode=yes iiotgw@iiotgw "mkdir -p '$REMOTE_RELEASE' && tar -xzf - -C '$REMOTE_RELEASE'"

echo "==> Create isolated venv and install runtime dependencies"
ssh -o BatchMode=yes iiotgw@iiotgw "bash -lc '
set -euo pipefail
cd "$REMOTE_RELEASE"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install ".[mqtt,edge,dev]"
.venv/bin/python -m pip check
export PYTHONPATH=src
export PYTHONDONTWRITEBYTECODE=1
.venv/bin/python -m compileall -q src tests run_gateway.py
.venv/bin/python -m unittest discover -s tests -p "test_*.py" -q
.venv/bin/python run_gateway.py check-config --config config/iiotgw.toml
'"

echo "==> Promote current symlink only after target QA passes"
ssh -o BatchMode=yes iiotgw@iiotgw "mkdir -p '$REMOTE_BASE/releases' && ln -sfn '$REMOTE_RELEASE' '$REMOTE_BASE/current'"

echo "DEPLOY_RELEASE=$RELEASE_ID"
