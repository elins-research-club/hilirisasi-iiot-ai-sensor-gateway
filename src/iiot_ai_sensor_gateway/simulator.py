from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCENARIOS = {'normal', 'voc_rise', 'battery_drop', 'missing_data', 'sensor_error', 'node_silent', 'sequence_gap', 'mixed'}

def make_payload(scenario: str, index: int, node_id: str, timestamp: datetime) -> dict:
    temp = 29.0 + random.uniform(-0.5, 0.5)
    hum = 65.0 + random.uniform(-2.0, 2.0)
    voc = 450.0 + random.uniform(-30.0, 30.0)
    batt = 4.05 - index * 0.001
    flags: list[str] = []
    status = 'ok'
    if scenario in {'voc_rise', 'mixed'}:
        voc += max(0, index - 10) * 45
    if scenario in {'battery_drop', 'mixed'}:
        batt -= max(0, index - 10) * 0.01
    seq = index + 3 if scenario == 'sequence_gap' and index > 12 else index
    if scenario == 'sensor_error' and index % 10 == 0:
        status = 'sensor_error'
        flags.append('sensor_error')
    sensor = {'tc': round(temp, 2), 'h': round(hum, 2), 'v': round(voc, 1), 'bv': round(batt, 3), 'ma': 82.0, 'mw': round(batt * 82.0, 2)}
    if scenario == 'missing_data' and index % 7 == 0:
        sensor.pop('v')
    return {'v': 1, 'gw': 'raspi_gateway_01', 'n': node_id, 'r': 'room_A', 'ts': timestamp.isoformat(), 'seq': seq, 'st': status, 'q': 'valid', 'f': flags, 's': sensor}

def iter_payloads(scenario: str, count: int, nodes: int, interval_sec: int, start: datetime):
    for index in range(count):
        for node_num in range(1, nodes + 1):
            if scenario == 'node_silent' and node_num == nodes and index > count // 2:
                continue
            node_id = f'node_{node_num:02d}'
            timestamp = start + timedelta(seconds=index * interval_sec)
            yield make_payload(scenario, index, node_id, timestamp)

def write_simulation(path: str | Path, scenario: str, count: int, nodes: int, interval_sec: int, seed: int = 7) -> Path:
    random.seed(seed)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    start = datetime.now(tz=UTC).replace(microsecond=0)
    rows = iter_payloads(scenario, count, nodes, interval_sec, start)
    output.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    return output
