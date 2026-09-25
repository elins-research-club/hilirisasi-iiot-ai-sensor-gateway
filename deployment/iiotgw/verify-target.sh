#!/usr/bin/env bash
set -euo pipefail

APP="$HOME/apps/iiot-ai-sensor-gateway/current"
PY="$APP/.venv/bin/python"

echo "=== RELEASE ==="
cat "$APP/deployment/RELEASE.txt"
echo "=== CONFIG ==="
"$PY" "$APP/run_gateway.py" check-config --config "$APP/config/iiotgw.toml"
echo "=== IMPORTS ==="
"$PY" - <<'PY'
import paho.mqtt.client
import numpy
import iiot_ai_sensor_gateway
print("imports=PASS")
print("numpy", numpy.__version__)
PY
echo "=== RUNTIME MODEL ARTIFACT SHA ==="
"$PY" - <<'PY'
import hashlib, json, pathlib
root=pathlib.Path.home()/"apps/iiot-ai-sensor-gateway/current"
m=json.loads((root/"deployment/model-manifests/co2_fits_pi5_20260828.json").read_text())
p=root/m["runtime_artifact_path"]
d=hashlib.sha256(p.read_bytes()).hexdigest()
print(d)
assert d == m["runtime_artifact_sha256"]
print("runtime_artifact_hash=PASS")
PY
echo "=== SERVICE ==="
systemctl --user show iiot-ai-sensor-gateway.service +  -p ActiveState -p SubState -p UnitFileState -p MainPID -p NRestarts --no-pager
