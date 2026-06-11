import importlib.util
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from iiot_ai_sensor_gateway.contracts import ResampledPoint, SensorValues
from iiot_ai_sensor_gateway.features import FEATURE_NAMES, extract_features

HAS_NUMPY = importlib.util.find_spec('numpy') is not None
HAS_TORCH = importlib.util.find_spec('torch') is not None
HAS_ML_DEPS = HAS_NUMPY and HAS_TORCH

from iiot_ai_sensor_gateway.forecasting import (
    TARGET_NAMES,
    build_lstm_forecaster,
    evaluate_lstm_forecast,
    predict_lstm_forecast,
    prepare_forecast_dataset,
    run_forecast_experiments,
    train_lstm_forecast,
)


def _write_windows(path: Path, nodes: int = 1, count: int = 18, timesteps: int = 4) -> None:
    rows = []
    base = datetime(2026, 6, 1, tzinfo=UTC)
    feature_count = len(FEATURE_NAMES)
    for node_index in range(nodes):
        node_id = f'node-{node_index + 1}'
        for index in range(count):
            start = base + timedelta(minutes=index)
            x = []
            for step in range(timesteps):
                values = []
                for feature_index in range(feature_count):
                    values.append(round(((index + step + feature_index + node_index) % 100) / 100, 6))
                x.append(values)
            rows.append({
                'gateway_id': 'gw-test',
                'node_id': node_id,
                'room_id': 'room-test',
                'start_timestamp': start.isoformat(),
                'end_timestamp': (start + timedelta(minutes=timesteps - 1)).isoformat(),
                'feature_names': list(FEATURE_NAMES),
                'shape': [timesteps, feature_count],
                'x': x,
            })
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')


@unittest.skipUnless(HAS_ML_DEPS, 'NumPy and PyTorch are optional ML dependencies')
class ForecastingTests(unittest.TestCase):
    def test_prepare_forecast_dataset_shapes_and_targets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            windows = temp / 'windows.jsonl'
            dataset = temp / 'dataset.npz'
            meta = temp / 'meta.json'
            _write_windows(windows, nodes=2, count=80, timesteps=4)

            stats = prepare_forecast_dataset(windows, dataset, meta, horizon_steps=2)

            self.assertEqual(stats.target_names, TARGET_NAMES)
            self.assertEqual(stats.input_shape[1:], (4, len(FEATURE_NAMES)))
            self.assertEqual(stats.target_shape, (stats.total_samples, len(TARGET_NAMES)))
            self.assertEqual(stats.train_samples + stats.val_samples + stats.test_samples, stats.total_samples)
            self.assertIn('train', stats.split_time_range)
            train_end = datetime.fromisoformat(stats.split_time_range['train']['label_end'])
            val_start = datetime.fromisoformat(stats.split_time_range['val']['input_start'])
            val_end = datetime.fromisoformat(stats.split_time_range['val']['label_end'])
            test_start = datetime.fromisoformat(stats.split_time_range['test']['input_start'])
            self.assertGreater(val_start, train_end)
            self.assertGreater(test_start, val_end)
            self.assertTrue(dataset.exists())
            self.assertTrue(meta.exists())

    def test_gas_zero_is_not_treated_as_missing(self):
        point = ResampledPoint(
            'gw',
            'node-1',
            'room',
            datetime(2026, 6, 1, tzinfo=UTC),
            SensorValues(voc_raw=0.0, bme_gas_raw=1200.0, co_raw=0.01),
            1.0,
            0,
            0,
        )
        vector = extract_features([point])[0]
        self.assertEqual(vector.values['gas_mean_3'], 0.0)
        self.assertEqual(vector.values['gas_delta'], 0.0)

    def test_lstm_forward_shape(self):
        import torch

        model = build_lstm_forecaster(input_size=len(FEATURE_NAMES), output_size=len(TARGET_NAMES), hidden_size=8)
        output = model(torch.zeros((3, 4, len(FEATURE_NAMES)), dtype=torch.float32))
        self.assertEqual(tuple(output.shape), (3, len(TARGET_NAMES)))

    def test_train_evaluate_predict_smoke(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            windows = temp / 'windows.jsonl'
            dataset = temp / 'dataset.npz'
            meta = temp / 'meta.json'
            model_dir = temp / 'model'
            predictions = temp / 'predictions.jsonl'
            _write_windows(windows, nodes=1, count=80, timesteps=4)
            prepare_forecast_dataset(windows, dataset, meta, horizon_steps=2)

            train = train_lstm_forecast(
                dataset,
                model_dir,
                epochs=2,
                batch_size=4,
                hidden_size=8,
                patience=2,
                device='cpu',
            )
            self.assertTrue(Path(train['model_path']).exists())

            metrics = evaluate_lstm_forecast(dataset, train['model_path'], model_dir, device='cpu')
            self.assertEqual(metrics['status'], 'PASS')
            self.assertEqual(metrics['data_status'], 'PASS')
            self.assertIn(metrics['baseline_comparison_status'], {'BEATS_BASELINE', 'UNDER_BASELINE', 'MIXED'})
            self.assertIn(metrics['model_readiness'], {'PROMISING', 'EXPERIMENTAL', 'NOT_READY'})
            self.assertIn('last_value_baseline', metrics['splits']['test'])
            self.assertIn('baseline_delta', metrics['splits']['test'])
            self.assertIn('denormalized', metrics['splits']['test']['lstm'])
            self.assertIn('rmse_skill_score', metrics['splits']['test']['baseline_delta']['per_target'][TARGET_NAMES[0]])
            self.assertEqual(metrics['nan_count'], 0)
            self.assertEqual(metrics['inf_count'], 0)

            output = predict_lstm_forecast(windows, train['model_path'], predictions, max_windows=3, device='cpu')
            lines = output.read_text(encoding='utf-8').splitlines()
            self.assertEqual(len(lines), 3)
            first = json.loads(lines[0])
            self.assertEqual(first['target_names'], list(TARGET_NAMES))
            self.assertEqual(len(first['prediction_normalized']), len(TARGET_NAMES))
            self.assertEqual(set(first['prediction_values']), set(TARGET_NAMES))
            self.assertEqual(first['model_version'], 'lstm_forecast_v1')

            payloads = temp / 'forecast_payloads.jsonl'
            from iiot_ai_sensor_gateway.forecasting import build_forecast_payload_v1

            build_forecast_payload_v1(predictions, model_dir / 'metrics.json', payloads)
            payload = json.loads(payloads.read_text(encoding='utf-8').splitlines()[0])
            self.assertEqual(payload['schema'], 'iiot.ai_sensor.forecast.v1')
            self.assertEqual(set(payload['predicted_sensor']), set(TARGET_NAMES))
            self.assertIn('model_readiness', payload)

    def test_run_forecast_experiments_writes_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            windows = temp / 'windows.jsonl'
            output_dir = temp / 'experiments'
            _write_windows(windows, nodes=1, count=80, timesteps=4)

            summary = run_forecast_experiments(
                windows,
                output_dir,
                horizons=(2,),
                hidden_sizes=(8,),
                epochs=1,
                batch_size=4,
                patience=1,
                device='cpu',
                window_sizes=(4, 6),
            )

            self.assertEqual(len(summary), 2)
            self.assertEqual(summary[0]['run_id'], 'w4_h2_hidden8')
            self.assertIn('temperature_c_rmse', summary[0])
            self.assertIn('temperature_c_denorm_rmse', summary[0])
            self.assertTrue((output_dir / 'summary.json').exists())
            self.assertTrue((output_dir / 'summary.csv').exists())
            self.assertTrue((output_dir / 'runs' / 'w4_h2_hidden8' / 'metrics.json').exists())


if __name__ == '__main__':
    unittest.main()
