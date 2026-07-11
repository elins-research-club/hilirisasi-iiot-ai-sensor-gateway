from __future__ import annotations

import json
import math
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCENARIOS = {
    "normal",
    "air_rise",
    "battery_drop",
    "missing_data",
    "sensor_error",
    "node_silent",
    "sequence_gap",
    "mixed",
}


def _node_phase(node_id: str) -> float:
    return (sum(ord(char) for char in node_id) % 31) / 31.0 * math.tau


def _event_pulse(index: int, *, period: int = 240, start: int = 40, width: int = 120) -> float:
    """Bounded rise-and-recovery pulse in [0, 1]."""

    position = index % period
    if position < start or position >= start + width:
        return 0.0
    progress = (position - start) / width
    return math.sin(math.pi * progress)


def _bounded(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def make_payload(scenario: str, index: int, node_id: str, timestamp: datetime) -> dict:
    if scenario not in SCENARIOS:
        raise ValueError(f"unsupported scenario: {scenario}")
    phase = _node_phase(node_id)
    daily = math.sin(math.tau * index / 1440.0 + phase)
    short_cycle = math.sin(math.tau * index / 90.0 + phase / 2.0)
    pulse = _event_pulse(index) if scenario in {"air_rise", "mixed"} else 0.0

    # Bounded, non-degenerate environmental dynamics. Random noise is seeded by
    # write_simulation(), while periodic/event terms make recovery explicit.
    temperature = 28.0 + 1.8 * daily + 0.25 * short_cycle + random.gauss(0.0, 0.12)
    humidity = 62.0 - 5.0 * daily + 0.8 * short_cycle + random.gauss(0.0, 0.35)
    pressure = 1008.0 + 2.2 * math.sin(math.tau * index / 720.0 + phase) + random.gauss(0.0, 0.12)
    bme_gas = 18_500.0 - 2_500.0 * pulse + 350.0 * short_cycle + random.gauss(0.0, 120.0)
    co_ppm = 1.8 + 5.5 * pulse + 0.18 * short_cycle + random.gauss(0.0, 0.07)
    co2_ppm = 620.0 + 900.0 * pulse + 35.0 * short_cycle + random.gauss(0.0, 8.0)
    pm25 = 10.0 + 65.0 * pulse + 2.0 * short_cycle + random.gauss(0.0, 0.9)
    o3_ppm = 0.028 + 0.018 * pulse + 0.002 * daily + random.gauss(0.0, 0.001)
    no2_mv = 410.0 + 90.0 * pulse + 8.0 * short_cycle + random.gauss(0.0, 2.0)

    cycle_age = index % 5000
    battery_drop = 0.00009 * cycle_age
    if scenario in {"battery_drop", "mixed"}:
        battery_drop += 0.18 * _event_pulse(index, period=360, start=90, width=180)
    battery = _bounded(4.12 - battery_drop + random.gauss(0.0, 0.003), 3.45, 4.2)

    flags: list[str] = []
    hardware_summary = "ok"
    sensor_status = {
        "bme688": "ok",
        "sen0466": "ok",
        "sen0574": "ok",
        "sen0321": "ok",
        "mhz19": "ok",
        "pms7003t": "ok",
        "ina226": "ok",
    }
    if index < 3:
        sensor_status["mhz19"] = "warming"
        sensor_status["pms7003t"] = "warming"
        hardware_summary = "warming"
        flags.extend(("co2_warmup", "pm_warmup"))
    if scenario == "sensor_error" and index % 29 == 0 and index >= 3:
        sensor_status["sen0321"] = "protocol_error"
        hardware_summary = "partial"
        flags.append("o3_protocol_error")

    current_ma = 78.0 + 7.0 * pulse + random.gauss(0.0, 0.8)
    sensor = {
        "tc": round(_bounded(temperature, -40.0, 85.0), 2),
        "h": round(_bounded(humidity, 0.0, 100.0), 2),
        "p": round(_bounded(pressure, 300.0, 1250.0), 2),
        "bme": round(_bounded(bme_gas, 1.0, 1_000_000_000.0), 1),
        "co": round(_bounded(co_ppm, 0.0, 10_000.0), 3),
        "n2mv": round(_bounded(no2_mv, 0.0, 3300.0), 2),
        "n2r": round(_bounded(no2_mv / 410.0, 0.0, 100.0), 4),
        "o3": None if sensor_status["sen0321"] != "ok" else round(_bounded(o3_ppm, 0.0, 100.0), 4),
        "co2": None if sensor_status["mhz19"] == "warming" else round(_bounded(co2_ppm, 0.0, 50_000.0), 1),
        "pm1": None if sensor_status["pms7003t"] == "warming" else round(_bounded(pm25 * 0.68, 0.0, 10_000.0), 1),
        "pm25": None if sensor_status["pms7003t"] == "warming" else round(_bounded(pm25, 0.0, 10_000.0), 1),
        "pm10": None if sensor_status["pms7003t"] == "warming" else round(_bounded(pm25 * 1.42, 0.0, 10_000.0), 1),
        "bv": round(battery, 3),
        "bi": round(current_ma, 2),
        "bp": round(battery * current_ma, 2),
    }
    if scenario == "missing_data" and index % 17 == 0 and index >= 3:
        sensor["co2"] = None
        sensor_status["mhz19"] = "timeout"
        hardware_summary = "partial"
        flags.append("co2_timeout")

    sequence = index + 3 if scenario == "sequence_gap" and index > 12 else index
    return {
        "v": 3,
        "gw": "raspi_gateway_01",
        "n": node_id,
        "r": "room_A",
        "ts": timestamp.isoformat(),
        "tb": "rfc3339",
        "seq": sequence,
        "bid": f"sim-{node_id}-boot-01",
        "pp": "hardware_only",
        "fw": "simulator-v3",
        "cfg": "simulation-bounded-v1",
        "cal": "synthetic-not-calibration",
        "hs": hardware_summary,
        "f": flags,
        "ok": sensor_status,
        "s": sensor,
    }


def iter_payloads(scenario: str, count: int, nodes: int, interval_sec: int, start: datetime):
    for index in range(count):
        for node_num in range(1, nodes + 1):
            if scenario == "node_silent" and node_num == nodes and index > count // 2:
                continue
            node_id = f"node_{node_num:02d}"
            timestamp = start + timedelta(seconds=index * interval_sec)
            yield make_payload(scenario, index, node_id, timestamp)


def write_simulation(
    path: str | Path,
    scenario: str,
    count: int,
    nodes: int,
    interval_sec: int,
    seed: int = 7,
) -> Path:
    if scenario not in SCENARIOS:
        raise ValueError(f"unsupported scenario: {scenario}")
    if count < 1 or nodes < 1 or interval_sec < 1:
        raise ValueError("count, nodes, and interval_sec must be positive")
    random.seed(seed)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    start = datetime.now(tz=UTC).replace(microsecond=0)
    rows = iter_payloads(scenario, count, nodes, interval_sec, start)
    output.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )
    return output
