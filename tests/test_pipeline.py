import csv
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from iiot_ai_sensor_gateway.adapters.gary_stafford import convert_gary_stafford_csv
from iiot_ai_sensor_gateway.adapters.gary_project_schema import derive_gary_project_schema
from iiot_ai_sensor_gateway.config import load_config
from iiot_ai_sensor_gateway.contracts import FeatureVector
from iiot_ai_sensor_gateway.evaluation import evaluate_preprocessing
from iiot_ai_sensor_gateway.esp32_sim import simulate_gary_esp32_payloads
from iiot_ai_sensor_gateway.features import FEATURE_NAMES
from iiot_ai_sensor_gateway.parser import PayloadParser
from iiot_ai_sensor_gateway.pipeline import PreModelPipeline
from iiot_ai_sensor_gateway.simulator import write_simulation
from iiot_ai_sensor_gateway.validation import ReadingValidator
from iiot_ai_sensor_gateway.windowing import WindowBuilder

class PipelineTests(unittest.TestCase):
    def test_parse_compact_payload(self):
        parser = PayloadParser('gw_default', 'room_default')
        reading = parser.parse({'gw': 'gw1', 'n': 'node1', 'r': 'room1', 'ts': '2026-05-29T10:00:00+07:00', 'seq': 7, 's': {'tc': 30.5, 'h': 66, 'co': 0.01}})
        self.assertEqual(reading.gateway_id, 'gw1')
        self.assertEqual(reading.node_id, 'node1')
        self.assertEqual(reading.sensor.co_raw, 0.01)

    def test_parse_esp32_firmware_payload_aliases(self):
        parser = PayloadParser('gw_default', 'room_default')
        reading = parser.parse({'n': 'node1', 'r': 'room1', 'ts': '2026-06-03T10:00:00+07:00', 'seq': 1, 'f': 'pressure_unavailable|gas_proxy_from_lpg_smoke', 's': {'tc': 29.2, 'h': 65.4, 'p': 1008.3, 'bme': 18125, 'co': 0.012}})
        self.assertEqual(reading.sensor.pressure_hpa, 1008.3)
        self.assertEqual(reading.sensor.bme_gas_raw, 18125)
        self.assertEqual(reading.sensor.co_raw, 0.012)
        self.assertEqual(reading.flags, ('pressure_unavailable', 'gas_proxy_from_lpg_smoke'))
    def test_validator_range_and_sequence_gap(self):
        parser = PayloadParser('gw', 'room')
        validator = ReadingValidator({'temperature_c': (-10, 80), 'humidity_pct': (0, 100), 'co_raw': (0, 0.05)})
        first = parser.parse({'n': 'node1', 'ts': '2026-05-29T10:00:00+07:00', 'seq': 1, 's': {'tc': 30, 'h': 60, 'co': 0.01}})
        second = parser.parse({'n': 'node1', 'ts': '2026-05-29T10:01:00+07:00', 'seq': 3, 's': {'tc': 90, 'h': 60, 'co': 0.01}})
        self.assertTrue(validator.validate(first).is_valid)
        result = validator.validate(second)
        self.assertFalse(result.is_valid)
        self.assertIn('range_temperature_c', result.issues)
        self.assertIn('sequence_gap', result.issues)

    def test_window_builder_shape(self):
        builder = WindowBuilder(window_size=3, step=1)
        window = None
        for idx in range(3):
            values = {name: float(idx) for name in FEATURE_NAMES}
            vector = FeatureVector('gw', 'node1', 'room', datetime(2026, 5, 29, tzinfo=UTC), values)
            window = builder.add(vector)
        self.assertIsNotNone(window)
        self.assertEqual(window.shape, (3, len(FEATURE_NAMES)))

    def test_pipeline_smoke(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            payloads = write_simulation(Path(temp_dir) / 'payloads.jsonl', 'mixed', count=20, nodes=1, interval_sec=60)
            config = load_config(ROOT / 'config/default.toml')
            pipeline = PreModelPipeline(config)
            windows = []
            for line in payloads.read_text(encoding='utf-8').splitlines():
                windows.extend(pipeline.process_payload(line))
            self.assertTrue(windows)
            self.assertEqual(windows[-1].shape[0], config.pipeline.window_size)
            self.assertGreater(windows[-1].shape[1], 5)

    def test_gary_adapter_and_evaluator_sample(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            csv_path = temp / 'gary.csv'
            with csv_path.open('w', newline='', encoding='utf-8') as file:
                writer = csv.DictWriter(file, fieldnames=['ts', 'device', 'co', 'humidity', 'light', 'lpg', 'motion', 'smoke', 'temp'])
                writer.writeheader()
                for idx in range(15):
                    writer.writerow({'ts': 1594512094 + idx * 60, 'device': 'dev1', 'co': 0.01, 'humidity': 60, 'light': 'false', 'lpg': 0.01, 'motion': 'false', 'smoke': 0.02, 'temp': 25})
            canonical = temp / 'canonical.jsonl'
            stats = convert_gary_stafford_csv(csv_path, canonical)
            self.assertEqual(stats.written_rows, 15)
            first = json.loads(canonical.read_text(encoding='utf-8').splitlines()[0])
            self.assertEqual(first['sensor']['co_raw'], 0.01)
            self.assertEqual(first['sensor']['bme_gas_raw'], 0.015)
            config = load_config(ROOT / 'config/default.toml')
            pipeline = PreModelPipeline(config)
            windows_path = temp / 'windows.jsonl'
            windows = []
            for line in canonical.read_text(encoding='utf-8').splitlines():
                windows.extend(pipeline.process_payload(line))
            windows_path.write_text(''.join(json.dumps(w.as_record()) + '\n' for w in windows), encoding='utf-8')
            result = evaluate_preprocessing(canonical, windows_path, config)
            self.assertEqual(result.pipeline_status, 'PASS')
            self.assertEqual(result.dataset_coverage_status, 'PARTIAL')
            self.assertEqual(result.lstm_readiness, 'READY_WITH_LIMITATIONS')
            self.assertEqual(result.window_count, 4)
            self.assertEqual(result.shape[1], config.pipeline.window_size)

    def test_gary_esp32_simulator_to_lstm_ready_windows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            csv_path = temp / 'gary.csv'
            with csv_path.open('w', newline='', encoding='utf-8') as file:
                writer = csv.DictWriter(file, fieldnames=['ts', 'device', 'co', 'humidity', 'light', 'lpg', 'motion', 'smoke', 'temp'])
                writer.writeheader()
                for idx in range(15):
                    writer.writerow({'ts': 1594512094 + idx * 60, 'device': 'dev1', 'co': 0.01, 'humidity': 60 + idx * 0.1, 'light': 'false', 'lpg': 0.01, 'motion': 'false', 'smoke': 0.02, 'temp': 25 + idx * 0.1})
            payloads = temp / 'gary_esp32_payloads.jsonl'
            stats = simulate_gary_esp32_payloads(csv_path, payloads)
            self.assertEqual(stats.written_rows, 15)
            first = json.loads(payloads.read_text(encoding='utf-8').splitlines()[0])
            self.assertEqual(first['v'], 1)
            self.assertIn('gas_proxy_from_lpg_smoke', first['f'])
            self.assertIn('bme', first['s'])
            self.assertNotIn('p', first['s'])

            config = load_config(ROOT / 'config/default.toml')
            pipeline = PreModelPipeline(config)
            windows_path = temp / 'windows.jsonl'
            windows = []
            for line in payloads.read_text(encoding='utf-8').splitlines():
                windows.extend(pipeline.process_payload(line))
            windows_path.write_text(''.join(json.dumps(w.as_record()) + '\n' for w in windows), encoding='utf-8')
            result = evaluate_preprocessing(
                payloads,
                windows_path,
                config,
                input_source='gary_esp32_simulated_lora_payload',
                simulation_layer='esp32_light_preprocessing',
                gateway_layer='raspberry_pi_pre_model_pipeline',
            )
            self.assertEqual(result.pipeline_status, 'PASS')
            self.assertEqual(result.dataset_coverage_status, 'PARTIAL')
            self.assertEqual(result.lstm_readiness, 'READY_WITH_LIMITATIONS')
            self.assertEqual(result.window_count, 4)
            self.assertEqual(result.shape, (4, config.pipeline.window_size, len(FEATURE_NAMES)))
            self.assertEqual(result.nan_count, 0)
            self.assertEqual(result.inf_count, 0)
            self.assertEqual(result.input_source, 'gary_esp32_simulated_lora_payload')

    def test_gary_project_schema_derivation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            csv_path = temp / 'gary.csv'
            with csv_path.open('w', newline='', encoding='utf-8') as file:
                writer = csv.DictWriter(file, fieldnames=['ts', 'device', 'co', 'humidity', 'light', 'lpg', 'motion', 'smoke', 'temp'])
                writer.writeheader()
                for idx in range(15):
                    writer.writerow({'ts': 1594512094 + idx * 60, 'device': 'dev1', 'co': 0.004 + idx * 0.0001, 'humidity': 60 + idx * 0.1, 'light': 'false', 'lpg': 0.006 + idx * 0.0001, 'motion': 'false', 'smoke': 0.018 + idx * 0.0002, 'temp': 25 + idx * 0.1})
            derived_csv = temp / 'derived.csv'
            derived_jsonl = temp / 'derived.jsonl'
            derived_payloads = temp / 'payloads.jsonl'
            stats = derive_gary_project_schema(csv_path, derived_csv, derived_jsonl, derived_payloads)
            self.assertEqual(stats.written_rows, 15)
            self.assertEqual(stats.pressure_profile, 'smooth')
            header = derived_csv.read_text(encoding='utf-8').splitlines()[0]
            self.assertEqual(header, 'timestamp,node_id,sequence,temperature_c,humidity_pct,pressure_hpa,bme_gas_raw,co_raw')
            with derived_csv.open(newline='', encoding='utf-8') as file:
                first_csv = next(csv.DictReader(file))
            self.assertIn('pressure_hpa', first_csv)
            self.assertGreater(float(first_csv['pressure_hpa']), 1000.0)
            self.assertGreater(float(first_csv['bme_gas_raw']), 0.0)

            config = load_config(ROOT / 'config/default.toml')
            pipeline = PreModelPipeline(config)
            windows = []
            for line in derived_payloads.read_text(encoding='utf-8').splitlines():
                windows.extend(pipeline.process_payload(line))
            windows_path = temp / 'windows.jsonl'
            windows_path.write_text(''.join(json.dumps(w.as_record()) + '\n' for w in windows), encoding='utf-8')
            result = evaluate_preprocessing(
                derived_payloads,
                windows_path,
                config,
                input_source='gary_derived_project_schema_payload',
                simulation_layer='project_schema_derivation',
                gateway_layer='raspberry_pi_pre_model_pipeline',
            )
            self.assertEqual(result.pipeline_status, 'PASS')
            self.assertEqual(result.dataset_coverage_status, 'FULL')
            self.assertEqual(result.lstm_readiness, 'READY')
            self.assertEqual(result.shape, (4, config.pipeline.window_size, len(FEATURE_NAMES)))

    def test_gary_project_schema_dynamic_pressure_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            csv_path = temp / 'gary.csv'
            with csv_path.open('w', newline='', encoding='utf-8') as file:
                writer = csv.DictWriter(file, fieldnames=['ts', 'device', 'co', 'humidity', 'light', 'lpg', 'motion', 'smoke', 'temp'])
                writer.writeheader()
                for idx in range(90):
                    writer.writerow({'ts': 1594512094 + idx * 60, 'device': 'dev1', 'co': 0.004 + idx * 0.00002, 'humidity': 58 + idx * 0.03, 'light': 'false', 'lpg': 0.006 + idx * 0.00002, 'motion': 'false', 'smoke': 0.018 + idx * 0.00004, 'temp': 24 + idx * 0.02})

            smooth_csv = temp / 'smooth.csv'
            dynamic_csv = temp / 'dynamic.csv'
            smooth_stats = derive_gary_project_schema(csv_path, smooth_csv, temp / 'smooth.jsonl', temp / 'smooth_payloads.jsonl')
            dynamic_stats = derive_gary_project_schema(
                csv_path,
                dynamic_csv,
                temp / 'dynamic.jsonl',
                temp / 'dynamic_payloads.jsonl',
                pressure_profile='dynamic',
            )

            self.assertEqual(dynamic_stats.pressure_profile, 'dynamic')
            self.assertGreater(dynamic_stats.pressure_max_hpa - dynamic_stats.pressure_min_hpa, smooth_stats.pressure_max_hpa - smooth_stats.pressure_min_hpa)
            with dynamic_csv.open(newline='', encoding='utf-8') as file:
                rows = list(csv.DictReader(file))
            self.assertIn('pressure_hpa', rows[0])
            self.assertGreater(float(rows[0]['pressure_hpa']), 1000.0)

if __name__ == '__main__':
    unittest.main()
