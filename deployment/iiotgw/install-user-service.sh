#!/usr/bin/env bash
set -euo pipefail

APP="$HOME/apps/iiot-ai-sensor-gateway/current"
UNIT_SRC="$APP/systemd/user/iiot-ai-sensor-gateway.service"
UNIT_DST="$HOME/.config/systemd/user/iiot-ai-sensor-gateway.service"
USB_UNIT_SRC="$APP/systemd/user/iiot-node-usb-release.service"
USB_UNIT_DST="$HOME/.config/systemd/user/iiot-node-usb-release.service"
ENV_DIR="$HOME/.config/iiot-ai-sensor-gateway"
STATE_DIR="$HOME/.local/state/iiot-ai-sensor-gateway"

mkdir -p "$(dirname "$UNIT_DST")" "$ENV_DIR" "$STATE_DIR"
install -m 0644 "$UNIT_SRC" "$UNIT_DST"
install -m 0644 "$USB_UNIT_SRC" "$USB_UNIT_DST"
if [[ ! -f "$ENV_DIR/runtime.env" ]]; then
  install -m 0600 "$APP/deployment/iiotgw/runtime.env.example" "$ENV_DIR/runtime.env"
fi
systemctl --user daemon-reload
systemctl --user disable --now iiot-ai-sensor-gateway.service >/dev/null 2>&1 || true
systemctl --user disable --now iiot-node-usb-release.service >/dev/null 2>&1 || true
systemctl --user is-enabled iiot-ai-sensor-gateway.service 2>/dev/null || true
systemctl --user is-active iiot-ai-sensor-gateway.service 2>/dev/null || true
systemctl --user is-enabled iiot-node-usb-release.service 2>/dev/null || true
systemctl --user is-active iiot-node-usb-release.service 2>/dev/null || true
echo "SERVICE_INSTALLED_INACTIVE=PASS"
