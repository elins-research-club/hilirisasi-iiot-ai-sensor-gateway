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
import torch
import iiot_ai_sensor_gateway
print("imports=PASS")
print("torch", torch.__version__)
PY
echo "=== MODEL SHA ==="
"$PY" - <<'PY'
import hashlib, json, pathlib
root=pathlib.Path.home()/"apps/iiot-ai-sensor-gateway/current"
m=json.loads((root/"deployment/model-manifests/co2_fits_pi5_20260828.json").read_text())
p=root/m["model_path"]
d=hashlib.sha256(p.read_bytes()).hexdigest()
print(d)
assert d == m["model_sha256"]
print("model_hash=PASS")
PY
echo "=== SERVICE ==="
systemctl --user show iiot-ai-sensor-gateway.service +  -p ActiveState -p SubState -p UnitFileState -p MainPID -p NRestarts --no-pager
