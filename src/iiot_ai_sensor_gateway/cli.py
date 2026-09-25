from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from .adapters.gary_stafford import convert_gary_stafford_csv
from .adapters.gary_project_schema import derive_gary_project_schema
from .anomaly_benchmark import (
    benchmark_detection_file,
    inject_labeled_normalized_fixture,
)
from .adapters.bristol_bme680 import adapt_bristol_bme680_csv
from .adapters.uci_air_quality import adapt_uci_air_quality_csv
from .adapters.zenodo_pm_reference import adapt_zenodo_pm_reference_csv
from .config import load_config, load_dotenv
from .evaluation import evaluate_preprocessing, write_evaluation_report
from .esp32_sim import simulate_gary_esp32_payloads
from .features import extract_features
from .forecasting import (
    FORECAST_STRATEGIES,
    build_forecast_decision_v1,
    build_forecast_payload_v1,
    evaluate_lstm_forecast,
    predict_lstm_forecast,
    prepare_forecast_dataset,
    run_forecast_experiments,
    select_best_forecast_model,
    train_lstm_forecast,
)
from .normalization import MinMaxNormalizer
from .live_runtime import LiveSensorRuntime
from .edge_forecasting import (
    MODEL_TYPES,
    evaluate_edge_forecast,
    predict_edge_forecast,
    train_edge_forecast,
)
from .parser import PayloadParser
from .preprocessing import GatewaySemanticPreprocessor
from .real import FileReplaySource, LiveReceiver, SerialLineSource
from .real.serial_source import SerialDependencyError
from .resampling import resample
from .simulator import SCENARIOS, write_simulation
from .streaming_detection import RiverDependencyError, run_streaming_detection
from .validation import ReadingValidator
from .windowing import WindowBuilder
from .window_paths import canonical_windows_path


def _parse_string_list(value: str) -> tuple[str, ...]:
    items = tuple(item.strip() for item in value.split(',') if item.strip())
    if not items:
        raise argparse.ArgumentTypeError('expected a comma-separated non-empty list')
    return items


def _parse_int_list(value: str) -> tuple[int, ...]:
    items = tuple(int(item.strip()) for item in value.split(',') if item.strip())
    if not items or any(item <= 0 for item in items):
        raise argparse.ArgumentTypeError('expected comma-separated positive integers')
    return items


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError('expected a positive integer')
    return parsed


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError('expected a non-negative integer')
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError('expected a positive number')
    return parsed


def _nonnegative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError('expected a non-negative number')
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='IIoT AI sensor gateway pre-model pipeline')
    sub = parser.add_subparsers(dest='cmd', required=True)
    run = sub.add_parser('run', help='process canonical JSONL payload input')
    run.add_argument('--config', default='config/default.toml')
    run.add_argument('--input-file', default='data/simulated/payloads.jsonl')
    run.add_argument('--output-dir', default='data/processed')
    run.add_argument('--max-payloads', type=int, default=0)
    sim = sub.add_parser('simulate', help='generate compact ESP32-C6 payloads')
    sim.add_argument('--scenario', choices=sorted(SCENARIOS), default='mixed')
    sim.add_argument('--count', type=_positive_int, default=120)
    sim.add_argument('--nodes', type=_positive_int, default=2)
    sim.add_argument('--interval-sec', type=_positive_int, default=60)
    sim.add_argument('--output', default='data/simulated/payloads.jsonl')
    uci = sub.add_parser('adapt-uci-air-quality', help='adapt UCI Air Quality without fabricating project sensor fields')
    uci.add_argument('--input-csv', required=True)
    uci.add_argument('--output', default='data/canonical/uci_air_quality.jsonl')
    uci.add_argument('--timezone', default='Europe/Rome')
    bristol = sub.add_parser('adapt-bristol-bme680', help='adapt Bristol indoor BME680 long-format CSV')
    bristol.add_argument('--input-csv', required=True)
    bristol.add_argument('--output', default='data/canonical/bristol_bme680.jsonl')
    zenodo_pm = sub.add_parser(
        'adapt-zenodo-pm-reference',
        help='adapt Zenodo 7198378 Fidas PM reference data without fabricating PMS7003T fields',
    )
    zenodo_pm.add_argument('--input-csv', required=True)
    zenodo_pm.add_argument(
        '--output', default='data/canonical/zenodo_fidas_pm_reference.jsonl'
    )
    zenodo_pm.add_argument('--timezone', default='UTC')
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
    ev.add_argument('--windows', default='data/processed/windows.jsonl')
    ev.add_argument('--output', default='data/evaluation/gary_preprocessing_eval.json')
    ev.add_argument('--input-source', default='gary_stafford_canonical_or_compact_payload')
    ev.add_argument('--simulation-layer', default='dataset_or_esp32_light_preprocessing')
    ev.add_argument('--gateway-layer', default='raspberry_pi_pre_model_pipeline')
    prep_forecast = sub.add_parser('prepare-forecast-dataset', help='build temporal X/y dataset for LSTM forecasting')
    prep_forecast.add_argument('--config', default='config/default.toml')
    prep_forecast.add_argument('--windows', default='data/processed/windows.jsonl')
    prep_forecast.add_argument('--output-npz', default='data/modeling/lstm_forecast_dataset.npz')
    prep_forecast.add_argument('--output-meta', default='data/modeling/lstm_forecast_dataset_meta.json')
    prep_forecast.add_argument('--horizon-steps', type=int, default=5)
    prep_forecast.add_argument('--window-size', type=int, default=0)
    prep_forecast.add_argument('--purge-gap-steps', type=int, default=-1)
    prep_forecast.add_argument('--cadence-sec', type=_nonnegative_float, default=0.0, help='0=infer from timestamps; positive value is validated against timestamps')
    prep_forecast.add_argument('--cadence-relative-tolerance', type=_positive_float, default=0.10)
    prep_forecast.add_argument('--max-irregular-fraction', type=_nonnegative_float, default=0.05)
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
    train_forecast.add_argument('--forecast-strategy', choices=FORECAST_STRATEGIES, default='residual')
    eval_forecast = sub.add_parser('evaluate-lstm-forecast', help='evaluate LSTM forecast metrics and baseline')
    eval_forecast.add_argument('--config', default='config/default.toml')
    eval_forecast.add_argument('--dataset', default='data/modeling/lstm_forecast_dataset.npz')
    eval_forecast.add_argument('--model', default='models/lstm_forecast/latest/model.pt')
    eval_forecast.add_argument('--output-dir', default=None)
    eval_forecast.add_argument('--device', default='auto')
    eval_forecast.add_argument('--eval-batch-size', type=int, default=1024)
    eval_forecast.add_argument('--seasonal-period', type=_nonnegative_int, default=0)
    pred_forecast = sub.add_parser('predict-lstm-forecast', help='predict normalized sensor targets from windows')
    pred_forecast.add_argument('--config', default='config/default.toml')
    pred_forecast.add_argument('--windows', default='data/processed/windows.jsonl')
    pred_forecast.add_argument('--model', default='models/lstm_forecast/latest/model.pt')
    pred_forecast.add_argument('--output', default='models/lstm_forecast/latest/predictions.jsonl')
    pred_forecast.add_argument('--max-windows', type=int, default=0)
    pred_forecast.add_argument('--device', default='auto')
    experiments = sub.add_parser('run-forecast-experiments', help='run LSTM forecasting experiments across horizons and hidden sizes')
    experiments.add_argument('--windows', default='data/processed/windows.jsonl')
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
    experiments.add_argument('--forecast-strategy', choices=FORECAST_STRATEGIES, default='residual')
    experiments.add_argument('--cadence-sec', type=_nonnegative_float, default=0.0, help='0=infer from timestamps')
    payload = sub.add_parser('build-forecast-payload-v1', help='build decision-layer-ready forecast payload JSONL')
    payload.add_argument('--predictions', default='models/lstm_forecast/latest/predictions.jsonl')
    payload.add_argument('--metrics', default='models/lstm_forecast/latest/metrics.json')
    payload.add_argument('--output', default='models/lstm_forecast/latest/forecast_payloads.jsonl')
    payload.add_argument('--metrics-ref', default=None)
    decision = sub.add_parser('build-forecast-decision-v1', help='build local rule-based forecast decision JSONL')
    decision.add_argument('--forecast-payloads', default='models/lstm_forecast/latest/forecast_payloads.jsonl')
    decision.add_argument('--output', default='models/lstm_forecast/latest/decision_payloads.jsonl')
    edge_train = sub.add_parser('train-edge-forecast', help='train FITS-inspired, FITS-official-style, or DLinear edge forecast candidate')
    edge_train.add_argument('--dataset', default='data/modeling/lstm_forecast_dataset.npz')
    edge_train.add_argument('--output-dir', default='models/edge_forecast/latest')
    edge_train.add_argument('--model-type', choices=MODEL_TYPES, default='fits')
    edge_train.add_argument('--epochs', type=_positive_int, default=30)
    edge_train.add_argument('--batch-size', type=_positive_int, default=64)
    edge_train.add_argument('--learning-rate', type=_positive_float, default=0.001)
    edge_train.add_argument('--patience', type=_positive_int, default=5)
    edge_train.add_argument('--seed', type=int, default=42)
    edge_train.add_argument('--device', default='auto')
    edge_train.add_argument('--frequency-bins', type=_nonnegative_int, default=0)
    edge_train.add_argument('--moving-average-kernel', type=_positive_int, default=3)
    edge_train.add_argument('--pred-len', type=_positive_int, default=1, help='FITS official-style prediction length (project default 1 for single-horizon labels)')
    edge_train.add_argument('--individual', action='store_true', help='per-channel frequency upsampler (fits_official)')
    edge_eval = sub.add_parser('evaluate-edge-forecast', help='evaluate edge forecast candidate against LastValue and SeasonalNaive')
    edge_eval.add_argument('--dataset', default='data/modeling/lstm_forecast_dataset.npz')
    edge_eval.add_argument('--model', default='models/edge_forecast/latest/model.pt')
    edge_eval.add_argument('--output-dir', default=None)
    edge_eval.add_argument('--seasonal-period', type=_nonnegative_int, default=0)
    edge_eval.add_argument('--batch-size', type=_positive_int, default=1024)
    edge_eval.add_argument('--device', default='auto')
    edge_predict = sub.add_parser('predict-edge-forecast', help='run safe edge forecast inference from a prepared split')
    edge_predict.add_argument('--dataset', default='data/modeling/lstm_forecast_dataset.npz')
    edge_predict.add_argument('--model', default='models/edge_forecast/latest/model.pt')
    edge_predict.add_argument('--output', default='models/edge_forecast/latest/predictions.jsonl')
    edge_predict.add_argument('--split', choices=('train', 'val', 'test'), default='test')
    edge_predict.add_argument('--max-samples', type=_nonnegative_int, default=0)
    edge_predict.add_argument('--device', default='auto')
    stream = sub.add_parser('stream-detect', help='run native robust streaming detection or optional River HST+ADWIN')
    stream.add_argument('--input', required=True)
    stream.add_argument('--backend', choices=('native', 'river'), default='native')
    stream.add_argument('--output', default='data/modeling/streaming_detection.jsonl')
    stream.add_argument('--feature-names', type=_parse_string_list, required=True)
    stream.add_argument('--warmup-samples', type=_positive_int, default=64)
    stream.add_argument('--anomaly-threshold', type=float, default=0.7)
    stream.add_argument('--n-trees', type=_positive_int, default=25)
    stream.add_argument('--height', type=_positive_int, default=8)
    stream.add_argument('--window-size', type=_positive_int, default=250)
    stream.add_argument('--seed', type=int, default=42)
    stream.add_argument('--adwin-delta', type=_positive_float, default=0.002)
    stream.add_argument('--z-scale', type=_positive_float, default=3.0)
    stream.add_argument('--page-hinkley-delta', type=float, default=0.005)
    stream.add_argument('--page-hinkley-threshold', type=_positive_float, default=0.25)
    inject_anomaly = sub.add_parser('inject-anomaly-fixture', help='create deterministic normalized event-injection fixture for harness validation')
    inject_anomaly.add_argument('--output', default='data/modeling/anomaly_fixture.jsonl')
    inject_anomaly.add_argument('--labels', default='data/modeling/anomaly_fixture_labels.json')
    inject_anomaly.add_argument('--sample-count', type=_positive_int, default=360)
    inject_anomaly.add_argument('--interval-sec', type=_positive_int, default=60)
    inject_anomaly.add_argument('--event-starts', type=_parse_int_list, default=(120, 260))
    inject_anomaly.add_argument('--event-length', type=_positive_int, default=12)
    benchmark_anomaly = sub.add_parser('benchmark-anomaly-events', help='evaluate timestamped anomaly decisions against event labels')
    benchmark_anomaly.add_argument('--detections', required=True)
    benchmark_anomaly.add_argument('--labels', required=True)
    benchmark_anomaly.add_argument('--output', default='data/modeling/anomaly_benchmark.json')
    benchmark_anomaly.add_argument('--merge-gap-sec', type=_nonnegative_float, default=0.0)
    benchmark_anomaly.add_argument('--match-tolerance-sec', type=_nonnegative_float, default=0.0)
    selector = sub.add_parser('select-best-forecast-model', help='select a forecast model candidate from experiment summary.csv')
    selector.add_argument('--summary', default='models/forecast_experiments/latest/summary.csv')
    selector.add_argument('--output', default='models/forecast_experiments/latest/best_model_selection.json')
    selector.add_argument('--top-k', type=int, default=5)
    selector.add_argument('--co-weight', type=float, default=0.20)
    selector.add_argument('--o3-weight', type=float, default=0.15)
    selector.add_argument('--co2-weight', type=float, default=0.15)
    selector.add_argument('--pm25-weight', type=float, default=0.15)
    selector.add_argument('--temperature-weight', type=float, default=0.10)
    selector.add_argument('--humidity-weight', type=float, default=0.08)
    selector.add_argument('--pressure-weight', type=float, default=0.07)
    selector.add_argument('--overall-weight', type=float, default=0.10)
    selector.add_argument('--require-data-status', default='PASS')
    selector.add_argument('--no-prefer-readiness', action='store_true')
    receiver = sub.add_parser('receive-real-live', help='receive real live LoRa/serial JSONL payloads or replay file')
    receiver.add_argument('--config', default='config/default.toml')
    receiver_source = receiver.add_mutually_exclusive_group(required=True)
    receiver_source.add_argument('--replay-file', default=None)
    receiver_source.add_argument('--port', default=None)
    receiver.add_argument('--baudrate', type=_positive_int, default=9600)
    receiver.add_argument('--timeout', type=_positive_float, default=1.0)
    receiver.add_argument('--max-messages', type=_nonnegative_int, default=0)
    receiver.add_argument('--output-dir', default='data/real_live_logs')
    receiver.add_argument('--rotate-max-bytes', type=_nonnegative_int, default=None)
    receiver.add_argument('--idle-sleep-sec', type=_positive_float, default=None)
    receiver.add_argument('--reconnect-initial-sec', type=_positive_float, default=None)
    receiver.add_argument('--reconnect-max-sec', type=_positive_float, default=None)
    live = sub.add_parser(
        'run-live-chirpstack',
        help='run ChirpStack MQTT live sensor pipeline (shadow/publish mode from config)',
    )
    live.add_argument('--config', default='config/iiotgw.toml')
    live.add_argument('--max-messages', type=_nonnegative_int, default=0)
    live.add_argument('--poll-timeout-sec', type=_positive_float, default=1.0)
    check = sub.add_parser('check-config', help='load config and exit')
    check.add_argument('--config', default='config/default.toml')
    return parser

def run_pipeline(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    parser = PayloadParser(
        config.identity.gateway_id,
        config.identity.default_room_id,
        allow_legacy_v1=config.contract.allow_legacy_v1,
    )
    validator = ReadingValidator(
        config.validation_ranges,
        config.pipeline.sequence_gap_warn,
        config.pipeline.validation_state_max_entries,
    )
    preprocessor = GatewaySemanticPreprocessor(
        version=config.preprocessing.version,
        filters=config.preprocessing.filters,
        apply_to_v2=config.preprocessing.apply_to_v2,
        state_max_entries=config.preprocessing.state_max_entries,
    )
    normalizer = MinMaxNormalizer(config.normalization_ranges)
    window_builder = WindowBuilder(config.pipeline.window_size, config.pipeline.window_step)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    by_node: dict[tuple[str, str, str], list] = defaultdict(list)
    count = 0
    with (
        (output_dir / 'raw_payloads.jsonl').open('w', encoding='utf-8') as raw_file,
        (output_dir / 'hardware_observations.jsonl').open('w', encoding='utf-8') as hardware_file,
        (output_dir / 'canonical_observations.jsonl').open('w', encoding='utf-8') as canonical_file,
    ):
        for line in Path(args.input_file).read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            raw_file.write(json.dumps({'payload': line}, separators=(',', ':')) + '\n')
            reading = parser.parse(line)
            hardware_file.write(json.dumps(reading.as_record(), separators=(',', ':')) + '\n')
            result = preprocessor.process(validator.validate(reading))
            canonical_file.write(json.dumps(result.as_record(), separators=(',', ':')) + '\n')
            by_node[(reading.gateway_id, reading.node_id, reading.room_id)].append(result)
            count += 1
            if args.max_payloads and count >= args.max_payloads:
                break
    with (
        (output_dir / 'processed_timeseries.jsonl').open('w', encoding='utf-8') as processed_file,
        canonical_windows_path(output_dir).open('w', encoding='utf-8') as windows_file,
    ):
        for identity in sorted(by_node):
            rows = sorted(by_node[identity], key=lambda item: item.reading.timestamp)
            points = resample(rows, config.pipeline.resample_interval_sec)
            for point in points:
                processed_file.write(
                    json.dumps(
                        {
                            'gateway_id': point.gateway_id,
                            'node_id': point.node_id,
                            'room_id': point.room_id,
                            'timestamp': point.timestamp.isoformat(),
                            'sensor': point.sensor.as_dict(),
                            'valid_ratio': point.valid_ratio,
                            'missing_count': point.missing_count,
                            'seq_gap_count': point.seq_gap_count,
                            'source_event_ids': list(point.source_event_ids),
                            'preprocessing_version': point.preprocessing_version,
                        },
                        separators=(',', ':'),
                    ) + '\n'
                )
            for vector in extract_features(points):
                window = window_builder.add(normalizer.normalize(vector))
                if window is None:
                    continue
                record = json.dumps(window.as_record(), separators=(',', ':'))
                windows_file.write(record + '\n')
    (output_dir / 'normalization_report.json').write_text(
        json.dumps(normalizer.report(), indent=2), encoding='utf-8'
    )
    return 0

def main(argv: list[str] | None = None) -> int:
    load_dotenv(Path('.env'))
    args = build_parser().parse_args(argv)
    if args.cmd == 'simulate':
        print(write_simulation(args.output, args.scenario, args.count, args.nodes, args.interval_sec))
        return 0
    if args.cmd == 'adapt-uci-air-quality':
        stats = adapt_uci_air_quality_csv(args.input_csv, args.output, timezone_name=args.timezone)
        print(json.dumps(stats.as_dict(), indent=2))
        return 0
    if args.cmd == 'adapt-bristol-bme680':
        stats = adapt_bristol_bme680_csv(args.input_csv, args.output)
        print(json.dumps(stats.as_dict(), indent=2))
        return 0
    if args.cmd == 'adapt-zenodo-pm-reference':
        stats = adapt_zenodo_pm_reference_csv(
            args.input_csv, args.output, timezone_name=args.timezone
        )
        print(json.dumps(stats.as_dict(), indent=2))
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
            resample_interval_sec=(args.cadence_sec if args.cadence_sec > 0 else None),
            window_size=args.window_size or None,
            purge_gap_steps=None if args.purge_gap_steps < 0 else args.purge_gap_steps,
            normalization_ranges=config.normalization_ranges,
            cadence_relative_tolerance=args.cadence_relative_tolerance,
            max_irregular_fraction=args.max_irregular_fraction,
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
            args.forecast_strategy,
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
            args.seasonal_period,
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
            (args.cadence_sec if args.cadence_sec > 0 else None),
            load_config(args.config).normalization_ranges,
            args.model_version,
            args.eval_batch_size,
            args.forecast_strategy,
        )
        print(json.dumps(result, indent=2))
        return 0
    if args.cmd == 'build-forecast-payload-v1':
        output = build_forecast_payload_v1(args.predictions, args.metrics, args.output, args.metrics_ref)
        print(output)
        return 0
    if args.cmd == 'build-forecast-decision-v1':
        output = build_forecast_decision_v1(args.forecast_payloads, args.output)
        print(output)
        return 0
    if args.cmd == 'train-edge-forecast':
        result = train_edge_forecast(
            args.dataset,
            args.output_dir,
            model_type=args.model_type,
            epochs=args.epochs,
            batch_size=args.batch_size,
            learning_rate=args.learning_rate,
            patience=args.patience,
            seed=args.seed,
            device=args.device,
            frequency_bins=args.frequency_bins,
            moving_average_kernel=args.moving_average_kernel,
            pred_len=getattr(args, 'pred_len', 1),
            individual=bool(getattr(args, 'individual', False)),
        )
        print(json.dumps(result, indent=2))
        return 0
    if args.cmd == 'evaluate-edge-forecast':
        result = evaluate_edge_forecast(
            args.dataset,
            args.model,
            args.output_dir,
            seasonal_period=args.seasonal_period,
            batch_size=args.batch_size,
            device=args.device,
        )
        print(json.dumps(result, indent=2))
        return 0
    if args.cmd == 'predict-edge-forecast':
        output = predict_edge_forecast(
            args.dataset,
            args.model,
            args.output,
            split=args.split,
            max_samples=args.max_samples,
            device=args.device,
        )
        print(output)
        return 0
    if args.cmd == 'inject-anomaly-fixture':
        result = inject_labeled_normalized_fixture(
            args.output,
            args.labels,
            sample_count=args.sample_count,
            interval_sec=args.interval_sec,
            event_start_indices=args.event_starts,
            event_length=args.event_length,
        )
        print(json.dumps(result, indent=2))
        return 0
    if args.cmd == 'benchmark-anomaly-events':
        result = benchmark_detection_file(
            args.detections,
            args.labels,
            args.output,
            merge_gap_sec=args.merge_gap_sec,
            match_tolerance_sec=args.match_tolerance_sec,
        )
        print(json.dumps(result, indent=2))
        return 0
    if args.cmd == 'stream-detect':
        try:
            result = run_streaming_detection(
                args.input,
                args.output,
                feature_names=args.feature_names,
                backend=args.backend,
                warmup_samples=args.warmup_samples,
                anomaly_threshold=args.anomaly_threshold,
                n_trees=args.n_trees,
                height=args.height,
                window_size=args.window_size,
                seed=args.seed,
                adwin_delta=args.adwin_delta,
                z_scale=args.z_scale,
                page_hinkley_delta=args.page_hinkley_delta,
                page_hinkley_threshold=args.page_hinkley_threshold,
            )
        except RiverDependencyError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2))
        return 0
    if args.cmd == 'select-best-forecast-model':
        result = select_best_forecast_model(
            args.summary,
            args.output,
            args.top_k,
            args.co_weight,
            args.o3_weight,
            args.co2_weight,
            args.pm25_weight,
            args.temperature_weight,
            args.humidity_weight,
            args.pressure_weight,
            args.overall_weight,
            args.require_data_status,
            not args.no_prefer_readiness,
        )
        print(json.dumps(result, indent=2))
        return 0
    if args.cmd == 'receive-real-live':
        config = load_config(args.config)
        payload_parser = PayloadParser(
            config.identity.gateway_id,
            config.identity.default_room_id,
            allow_legacy_v1=config.contract.allow_legacy_v1,
        )
        validator = ReadingValidator(
            config.validation_ranges,
            config.pipeline.sequence_gap_warn,
            config.pipeline.validation_state_max_entries,
        )
        source = (
            FileReplaySource(args.replay_file)
            if args.replay_file
            else SerialLineSource(
                args.port,
                args.baudrate,
                args.timeout,
                idle_sleep_sec=args.idle_sleep_sec or config.receiver.serial_idle_sleep_sec,
                reconnect_initial_sec=(
                    args.reconnect_initial_sec or config.receiver.reconnect_initial_sec
                ),
                reconnect_max_sec=args.reconnect_max_sec or config.receiver.reconnect_max_sec,
            )
        )
        try:
            summary = LiveReceiver(payload_parser, validator).run(
                source,
                args.output_dir,
                args.max_messages,
                rotate_max_bytes=(
                    config.receiver.rotate_max_bytes
                    if args.rotate_max_bytes is None
                    else args.rotate_max_bytes
                ),
            )
        except SerialDependencyError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps(summary.as_dict(), indent=2))
        return 0
    if args.cmd == 'run-live-chirpstack':
        config = load_config(args.config)
        summary = LiveSensorRuntime(config).run(
            max_messages=args.max_messages,
            poll_timeout_sec=args.poll_timeout_sec,
        )
        print(json.dumps(summary, indent=2))
        return 0
    if args.cmd == 'check-config':
        load_config(args.config)
        print('config ok')
        return 0
    return run_pipeline(args)
