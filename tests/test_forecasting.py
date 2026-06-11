import importlib.util
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from iiot_ai_sensor_gateway.features import FEATURE_NAMES

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
            _write_windows(windows, nodes=2, count=20, timesteps=4)

            stats = prepare_forecast_dataset(windows, dataset, meta, horizon_steps=2)

            self.assertEqual(stats.target_names, TARGET_NAMES)
            self.assertEqual(stats.input_shape, (36, 4, len(FEATURE_NAMES)))
            self.assertEqual(stats.target_shape, (36, len(TARGET_NAMES)))
            self.assertEqual(stats.train_samples + stats.val_samples + stats.test_samples, 36)
            self.assertTrue(dataset.exists())
            self.assertTrue(meta.exists())

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
            _write_windows(windows, nodes=1, count=18, timesteps=4)
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
            self.assertEqual(metrics['nan_count'], 0)
            self.assertEqual(metrics['inf_count'], 0)

            output = predict_lstm_forecast(windows, train['model_path'], predictions, max_windows=3, device='cpu')
            lines = output.read_text(encoding='utf-8').splitlines()
            self.assertEqual(len(lines), 3)
            first = json.loads(lines[0])
            self.assertEqual(first['target_names'], list(TARGET_NAMES))
            self.assertEqual(len(first['prediction_normalized']), len(TARGET_NAMES))
            self.assertEqual(set(first['prediction_values']), set(TARGET_NAMES))

    def test_run_forecast_experiments_writes_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            windows = temp / 'windows.jsonl'
            output_dir = temp / 'experiments'
            _write_windows(windows, nodes=1, count=18, timesteps=4)

            summary = run_forecast_experiments(
                windows,
                output_dir,
                horizons=(2,),
                hidden_sizes=(8,),
                epochs=1,
                batch_size=4,
                patience=1,
                device='cpu',
            )

            self.assertEqual(len(summary), 1)
            self.assertEqual(summary[0]['run_id'], 'h2_hidden8')
            self.assertTrue((output_dir / 'summary.json').exists())
            self.assertTrue((output_dir / 'summary.csv').exists())
            self.assertTrue((output_dir / 'runs' / 'h2_hidden8' / 'metrics.json').exists())


if __name__ == '__main__':
    unittest.main()
