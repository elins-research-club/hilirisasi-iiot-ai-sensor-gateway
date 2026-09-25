#!/usr/bin/env python3
"""Release USB-UART control lines for the two current LoRaWAN ESP nodes.

Cold reboot evidence on 2026-09-25 showed both CH9102-attached ESP nodes are
power-reset with the Raspberry Pi and remain silent until their serial bridge
is opened with DTR/RTS deasserted. This helper performs exactly that action
once after boot. It does not flash firmware, write serial payloads, touch the
WM1302, or modify HardProg files.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Callable

DEFAULT_PORTS = (
    "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5926016290-if00",
    "/dev/serial/by-id/usb-1a86_USB_Single_Serial_58EF071105-if00",
)

SAFE_MARKERS = (
    "POWERON_RESET",
    "Joining LoRaWAN Network",
    "Join Success",
)


def wait_for_paths(
    paths: tuple[str, ...],
    *,
    timeout_sec: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    deadline = monotonic() + timeout_sec
    missing = [path for path in paths if not Path(path).exists()]
    while missing and monotonic() < deadline:
        sleep(0.25)
        missing = [path for path in paths if not Path(path).exists()]
    if missing:
        raise FileNotFoundError(f"USB LoRaWAN node ports not found: {missing}")


def recover_port(
    path: str,
    *,
    serial_factory: Callable[[], Any],
    hold_sec: float,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    serial_port = serial_factory()
    serial_port.port = path
    serial_port.baudrate = 115200
    serial_port.timeout = 0.20
    serial_port.dtr = False
    serial_port.rts = False
    if hasattr(serial_port, "exclusive"):
        serial_port.exclusive = True

    markers: list[str] = []
    lines_seen = 0
    try:
        serial_port.open()
        deadline = monotonic() + hold_sec
        while monotonic() < deadline:
            raw = serial_port.readline()
            if not raw:
                continue
            lines_seen += 1
            text = raw.decode("utf-8", errors="replace").strip()
            for marker in SAFE_MARKERS:
                if marker in text and marker not in markers:
                    markers.append(marker)
    finally:
        if getattr(serial_port, "is_open", False):
            serial_port.close()

    return {
        "path": path,
        "opened": True,
        "lines_seen": lines_seen,
        "markers": markers,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", action="append", dest="ports")
    parser.add_argument("--wait-for-devices-sec", type=float, default=60.0)
    parser.add_argument("--hold-sec", type=float, default=5.0)
    args = parser.parse_args()

    ports = tuple(args.ports or DEFAULT_PORTS)
    if not ports or args.wait_for_devices_sec <= 0 or args.hold_sec <= 0:
        parser.error("ports and timeout values must be positive/non-empty")

    wait_for_paths(ports, timeout_sec=args.wait_for_devices_sec)

    try:
        import serial
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise SystemExit("pyserial is required; install project extra 'serial'") from exc

    results = [
        recover_port(path, serial_factory=serial.Serial, hold_sec=args.hold_sec)
        for path in ports
    ]
    print(
        json.dumps(
            {
                "schema": "iiot.ops.usb_lorawan_recovery.v1",
                "status": "PASS",
                "ports": results,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
