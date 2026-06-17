from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from .adapters.gary_stafford import convert_gary_stafford_csv
from .adapters.gary_project_schema import derive_gary_project_schema
from .config import load_config, load_dotenv
from .evaluation import evaluate_preprocessing, write_evaluation_report
from .esp32_sim import simulate_gary_esp32_payloads
from .features import extract_features
from .forecasting import (
    build_forecast_payload_v1,
    evaluate_lstm_forecast,
    predict_lstm_forecast,
    prepare_forecast_dataset,
    run_forecast_experiments,
    select_best_forecast_model,
    train_lstm_forecast,
)
from .normalization import MinMaxNormalizer
from .parser import PayloadParser
from .resampling import resample
from .simulator import SCENARIOS, write_simulation
from .validation import ReadingValidator
from .windowing import WindowBuilder


def _parse_int_list(value: str) -> tuple[int, ...]:
    items = tuple(int(item.strip()) for item in value.split(',') if item.strip())
    if not items:
        raise argparse.ArgumentTypeError('expected comma-separated integers')
    return items

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
    derived = sub.add_parser('derive-gary-schema', help='derive project sensor schema dataset from Gary Stafford CSV')
    derived.add_argument('--input-csv', default='iot_telemetry_data.csv')
    derived.add_argument('--output-csv', default='data/derived/gary_project_sensor_schema.csv')
    derived.add_argument('--output-jsonl', default='data/derived/gary_project_sensor_schema.jsonl')
    derived.add_argument('--output-payloads', default='data/derived/gary_project_sensor_payloads.jsonl')
    derived.add_argument('--gateway-id', default='gary_project_schema')
    derived.add_argument('--room-id', default='gary_public')
    derived.add_argument('--pressure-profile', choices=('smooth', 'dynamic'), default='smooth')
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
    prep_forecast = sub.add_parser('prepare-forecast-dataset', help='build temporal X/y dataset for LSTM forecasting')
    prep_forecast.add_argument('--config', default='config/default.toml')
    prep_forecast.add_argument('--windows', default='data/processed/lstm_windows.jsonl')
    prep_forecast.add_argument('--output-npz', default='data/modeling/lstm_forecast_dataset.npz')
    prep_forecast.add_argument('--output-meta', default='data/modeling/lstm_forecast_dataset_meta.json')
    prep_forecast.add_argument('--horizon-steps', type=int, default=5)
    prep_forecast.add_argument('--window-size', type=int, default=0)
    prep_forecast.add_argument('--purge-gap-steps', type=int, default=-1)
    train_forecast = sub.add_parser('train-lstm-forecast', help='train PyTorch LSTM multi-target forecasting model')
    train_forecast.add_argument('--dataset', default='data/modeling/lstm_forecast_dataset.npz')
    train_forecast.add_argument('--output-dir', default='models/lstm_forecast/latest')
    train_forecast.add_argument('--epochs', type=int, default=30)
    train_forecast.add_argument('--batch-size', type=int, default=64)
    train_forecast.add_argument('--learning-rate', type=float, default=0.001)
    train_forecast.add_argument('--hidden-size', type=int, default=64)
    train_forecast.add_argument('--num-layers', type=int, default=1)
    train_forecast.add_argument('--patience', type=int, default=5)
    train_forecast.add_argument('--seed', type=int, default=42)
    train_forecast.add_argument('--device', default='auto')
    train_forecast.add_argument('--model-version', default='lstm_forecast_v1')
    eval_forecast = sub.add_parser('evaluate-lstm-forecast', help='evaluate LSTM forecast metrics and baseline')
    eval_forecast.add_argument('--config', default='config/default.toml')
    eval_forecast.add_argument('--dataset', default='data/modeling/lstm_forecast_dataset.npz')
    eval_forecast.add_argument('--model', default='models/lstm_forecast/latest/model.pt')
    eval_forecast.add_argument('--output-dir', default=None)
    eval_forecast.add_argument('--device', default='auto')
    eval_forecast.add_argument('--eval-batch-size', type=int, default=1024)
    pred_forecast = sub.add_parser('predict-lstm-forecast', help='predict normalized sensor targets from windows')
    pred_forecast.add_argument('--config', default='config/default.toml')
    pred_forecast.add_argument('--windows', default='data/processed/lstm_windows.jsonl')
    pred_forecast.add_argument('--model', default='models/lstm_forecast/latest/model.pt')
    pred_forecast.add_argument('--output', default='models/lstm_forecast/latest/predictions.jsonl')
    pred_forecast.add_argument('--max-windows', type=int, default=0)
    pred_forecast.add_argument('--device', default='auto')
    experiments = sub.add_parser('run-forecast-experiments', help='run LSTM forecasting experiments across horizons and hidden sizes')
    experiments.add_argument('--windows', default='data/processed/lstm_windows.jsonl')
    experiments.add_argument('--output-dir', default='models/forecast_experiments/latest')
    experiments.add_argument('--horizons', type=_parse_int_list, default=(5, 15, 30))
    experiments.add_argument('--hidden-sizes', type=_parse_int_list, default=(32, 64))
    experiments.add_argument('--window-sizes', type=_parse_int_list, default=(12,))
    experiments.add_argument('--config', default='config/default.toml')
    experiments.add_argument('--epochs', type=int, default=30)
    experiments.add_argument('--batch-size', type=int, default=64)
    experiments.add_argument('--learning-rate', type=float, default=0.001)
    experiments.add_argument('--num-layers', type=int, default=1)
    experiments.add_argument('--patience', type=int, default=5)
    experiments.add_argument('--seed', type=int, default=42)
    experiments.add_argument('--device', default='auto')
    experiments.add_argument('--model-version', default='lstm_forecast_v1')
    experiments.add_argument('--eval-batch-size', type=int, default=1024)
    payload = sub.add_parser('build-forecast-payload-v1', help='build decision-layer-ready forecast payload JSONL')
    payload.add_argument('--predictions', default='models/lstm_forecast/latest/predictions.jsonl')
    payload.add_argument('--metrics', default='models/lstm_forecast/latest/metrics.json')
    payload.add_argument('--output', default='models/lstm_forecast/latest/forecast_payloads.jsonl')
    payload.add_argument('--metrics-ref', default=None)
    selector = sub.add_parser('select-best-forecast-model', help='select a forecast model candidate from experiment summary.csv')
    selector.add_argument('--summary', default='models/forecast_experiments/latest/summary.csv')
    selector.add_argument('--output', default='models/forecast_experiments/latest/best_model_selection.json')
    selector.add_argument('--top-k', type=int, default=5)
    selector.add_argument('--gas-weight', type=float, default=0.25)
    selector.add_argument('--co-weight', type=float, default=0.25)
    selector.add_argument('--temperature-weight', type=float, default=0.15)
    selector.add_argument('--humidity-weight', type=float, default=0.15)
    selector.add_argument('--pressure-weight', type=float, default=0.10)
    selector.add_argument('--overall-weight', type=float, default=0.10)
    selector.add_argument('--require-data-status', default='PASS')
    selector.add_argument('--no-prefer-readiness', action='store_true')
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
    if args.cmd == 'derive-gary-schema':
        stats = derive_gary_project_schema(
            args.input_csv,
            args.output_csv,
            args.output_jsonl,
            args.output_payloads,
            args.gateway_id,
            args.room_id,
            args.pressure_profile,
        )
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
    if args.cmd == 'prepare-forecast-dataset':
        config = load_config(args.config)
        stats = prepare_forecast_dataset(
            args.windows,
            args.output_npz,
            args.output_meta,
            args.horizon_steps,
            resample_interval_sec=config.pipeline.resample_interval_sec,
            window_size=args.window_size or None,
            purge_gap_steps=None if args.purge_gap_steps < 0 else args.purge_gap_steps,
            normalization_ranges=config.normalization_ranges,
        )
        print(json.dumps(stats.as_dict(), indent=2))
        return 0
    if args.cmd == 'train-lstm-forecast':
        result = train_lstm_forecast(
            args.dataset,
            args.output_dir,
            args.epochs,
            args.batch_size,
            args.learning_rate,
            args.hidden_size,
            args.num_layers,
            args.patience,
            args.seed,
            args.device,
            args.model_version,
        )
        print(json.dumps(result, indent=2))
        return 0
    if args.cmd == 'evaluate-lstm-forecast':
        config = load_config(args.config)
        result = evaluate_lstm_forecast(
            args.dataset,
            args.model,
            args.output_dir,
            args.device,
            config.normalization_ranges,
            args.eval_batch_size,
        )
        print(json.dumps(result, indent=2))
        return 0
    if args.cmd == 'predict-lstm-forecast':
        config = load_config(args.config)
        output = predict_lstm_forecast(args.windows, args.model, args.output, args.max_windows, args.device, config.normalization_ranges)
        print(output)
        return 0
    if args.cmd == 'run-forecast-experiments':
        result = run_forecast_experiments(
            args.windows,
            args.output_dir,
            args.horizons,
            args.hidden_sizes,
            args.epochs,
            args.batch_size,
            args.learning_rate,
            args.num_layers,
            args.patience,
            args.seed,
            args.device,
            args.window_sizes,
            load_config(args.config).pipeline.resample_interval_sec,
            load_config(args.config).normalization_ranges,
            args.model_version,
            args.eval_batch_size,
        )
        print(json.dumps(result, indent=2))
        return 0
    if args.cmd == 'build-forecast-payload-v1':
        output = build_forecast_payload_v1(args.predictions, args.metrics, args.output, args.metrics_ref)
        print(output)
        return 0
    if args.cmd == 'select-best-forecast-model':
        result = select_best_forecast_model(
            args.summary,
            args.output,
            args.top_k,
            args.gas_weight,
            args.co_weight,
            args.temperature_weight,
            args.humidity_weight,
            args.pressure_weight,
            args.overall_weight,
            args.require_data_status,
            not args.no_prefer_readiness,
        )
        print(json.dumps(result, indent=2))
        return 0
    if args.cmd == 'check-config':
        load_config()
        print('config ok')
        return 0
    return run_pipeline(args)
