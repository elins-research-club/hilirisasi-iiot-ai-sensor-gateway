from __future__ import annotations

import json
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


def make_payload(scenario: str, index: int, node_id: str, timestamp: datetime) -> dict:
    temperature = 29.0 + random.uniform(-0.5, 0.5)
    humidity = 65.0 + random.uniform(-2.0, 2.0)
    bme_gas = 18_000.0 + random.uniform(-500.0, 500.0)
    co_ppm = 2.0 + random.uniform(-0.2, 0.2)
    co2_ppm = 650.0 + random.uniform(-20.0, 20.0)
    pm25 = 12.0 + random.uniform(-2.0, 2.0)
    battery = 4.05 - index * 0.001
    flags: list[str] = []
    status = "ok"
    quality = "valid"
    if scenario in {"air_rise", "mixed"}:
        rise = max(0, index - 10)
        co_ppm += rise * 0.25
        co2_ppm += rise * 10.0
        pm25 += rise * 1.5
    if scenario in {"battery_drop", "mixed"}:
        battery -= max(0, index - 10) * 0.01
    sequence = index + 3 if scenario == "sequence_gap" and index > 12 else index
    sensor_status = {
        "bme688": "ok",
        "sen0466": "ok",
        "sen0574": "ok",
        "sen0321": "ok",
        "mhz19": "ok",
        "pms7003t": "ok",
        "ina226": "ok",
    }
    if scenario == "sensor_error" and index % 10 == 0:
        status = "degraded"
        quality = "partial"
        sensor_status["sen0321"] = "error"
        flags.append("o3_read_error")
    current_ma = 82.0
    sensor = {
        "tc": round(temperature, 2),
        "h": round(humidity, 2),
        "p": round(1008.0 + random.uniform(-1.0, 1.0), 2),
        "bme": round(bme_gas, 1),
        "co": round(co_ppm, 3),
        "n2mv": round(420.0 + random.uniform(-10.0, 10.0), 2),
        "n2r": round(1.0 + random.uniform(-0.05, 0.05), 4),
        "o3": None if sensor_status["sen0321"] == "error" else round(0.03 + random.uniform(-0.005, 0.005), 4),
        "co2": round(co2_ppm, 1),
        "pm1": round(max(0.0, pm25 * 0.7), 1),
        "pm25": round(max(0.0, pm25), 1),
        "pm10": round(max(0.0, pm25 * 1.4), 1),
        "bv": round(battery, 3),
        "bi": current_ma,
        "bp": round(battery * current_ma, 2),
    }
    if scenario == "missing_data" and index % 7 == 0:
        sensor["co2"] = None
        sensor_status["mhz19"] = "missing"
        status = "degraded"
        quality = "partial"
        flags.append("co2_missing")
    return {
        "v": 2,
        "gw": "raspi_gateway_01",
        "n": node_id,
        "r": "room_A",
        "ts": timestamp.isoformat(),
        "seq": sequence,
        "bid": f"sim-{node_id}-boot-01",
        "st": status,
        "q": quality,
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
    output.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    return output
