from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from .adapters.gary_stafford import convert_gary_stafford_csv
from .config import load_config, load_dotenv
from .evaluation import evaluate_preprocessing, write_evaluation_report
from .esp32_sim import simulate_gary_esp32_payloads
from .features import extract_features
from .normalization import MinMaxNormalizer
from .parser import PayloadParser
from .resampling import resample
from .simulator import SCENARIOS, write_simulation
from .validation import ReadingValidator
from .windowing import WindowBuilder

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='IIoT AI sensor gateway pre-model pipeline')
    sub = parser.add_subparsers(dest='cmd', required=False)
    run = sub.add_parser('run', help='process canonical JSONL payload input')
    run.add_argument('--config', default='config/default.toml')
    run.add_argument('--input-file', default='data/simulated/payloads.jsonl')
    run.add_argument('--output-dir', default='data/processed')
    run.add_argument('--max-payloads', type=int, default=0)
    sim = sub.add_parser('simulate', help='generate compact ESP32-C6 payloads')
    sim.add_argument('--scenario', choices=sorted(SCENARIOS), default='mixed')
    sim.add_argument('--count', type=int, default=120)
    sim.add_argument('--nodes', type=int, default=2)
    sim.add_argument('--interval-sec', type=int, default=60)
    sim.add_argument('--output', default='data/simulated/payloads.jsonl')
    gary = sub.add_parser('convert-gary', help='convert Gary Stafford CSV to canonical JSONL')
    gary.add_argument('--input-csv', default='iot_telemetry_data.csv')
    gary.add_argument('--output', default='data/canonical/gary_stafford_canonical.jsonl')
    gary_esp32 = sub.add_parser('simulate-gary-esp32', help='simulate ESP32-C6 preprocessing from Gary CSV into compact LoRa JSONL')
    gary_esp32.add_argument('--input-csv', default='iot_telemetry_data.csv')
    gary_esp32.add_argument('--output', default='data/simulated/gary_esp32_lora_payloads.jsonl')
    gary_esp32.add_argument('--room-id', default='gary_public')
    gary_esp32.add_argument('--moving-average-window', type=int, default=3)
    ev = sub.add_parser('evaluate', help='evaluate canonical preprocessing output')
    ev.add_argument('--config', default='config/default.toml')
    ev.add_argument('--canonical', default='data/canonical/gary_stafford_canonical.jsonl')
    ev.add_argument('--windows', default='data/processed/lstm_windows.jsonl')
    ev.add_argument('--output', default='data/evaluation/gary_preprocessing_eval.json')
    ev.add_argument('--input-source', default='gary_stafford_canonical_or_compact_payload')
    ev.add_argument('--simulation-layer', default='dataset_or_esp32_light_preprocessing')
    ev.add_argument('--gateway-layer', default='raspberry_pi_pre_model_pipeline')
    sub.add_parser('check-config', help='load config and exit')
    return parser

def run_pipeline(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    parser = PayloadParser(config.identity.gateway_id, config.identity.default_room_id)
    validator = ReadingValidator(config.validation_ranges, config.pipeline.sequence_gap_warn)
    normalizer = MinMaxNormalizer(config.normalization_ranges)
    window_builder = WindowBuilder(config.pipeline.window_size, config.pipeline.window_step)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    by_node = defaultdict(list)
    count = 0
    with (output_dir / 'raw_payloads.jsonl').open('w', encoding='utf-8') as raw_file:
        for line in Path(args.input_file).read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            raw_file.write(json.dumps({'payload': line}, separators=(',', ':')) + '\n')
            reading = parser.parse(line)
            result = validator.validate(reading)
            by_node[reading.node_id].append(result)
            count += 1
            if args.max_payloads and count >= args.max_payloads:
                break
    with (output_dir / 'windows.jsonl').open('w', encoding='utf-8') as windows_file, (output_dir / 'lstm_windows.jsonl').open('w', encoding='utf-8') as dataset_file:
        for node_id in sorted(by_node):
            rows = sorted(by_node[node_id], key=lambda item: item.reading.timestamp)
            points = resample(rows, config.pipeline.resample_interval_sec)
            for vector in extract_features(points):
                window = window_builder.add(normalizer.normalize(vector))
                if window is None:
                    continue
                record = json.dumps(window.as_record(), separators=(',', ':'))
                windows_file.write(record + '\n')
                dataset_file.write(record + '\n')
    return 0

def main(argv: list[str] | None = None) -> int:
    load_dotenv(Path('.env'))
    args = build_parser().parse_args(argv)
    if args.cmd == 'simulate':
        print(write_simulation(args.output, args.scenario, args.count, args.nodes, args.interval_sec))
        return 0
    if args.cmd == 'convert-gary':
        stats = convert_gary_stafford_csv(args.input_csv, args.output)
        print(json.dumps(stats.as_dict(), indent=2))
        return 0
    if args.cmd == 'simulate-gary-esp32':
        stats = simulate_gary_esp32_payloads(args.input_csv, args.output, args.room_id, args.moving_average_window)
        print(json.dumps(stats.as_dict(), indent=2))
        return 0
    if args.cmd == 'evaluate':
        result = evaluate_preprocessing(
            args.canonical,
            args.windows,
            load_config(args.config),
            input_source=args.input_source,
            simulation_layer=args.simulation_layer,
            gateway_layer=args.gateway_layer,
        )
        write_evaluation_report(result, args.output)
        print(json.dumps(result.as_dict(), indent=2))
        return 0
    if args.cmd == 'check-config':
        load_config()
        print('config ok')
        return 0
    return run_pipeline(args)
