import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iiot_ai_sensor_gateway.adapters.bristol_bme680 import adapt_bristol_bme680_csv
from iiot_ai_sensor_gateway.adapters.uci_air_quality import adapt_uci_air_quality_csv
from iiot_ai_sensor_gateway.adapters.zenodo_pm_reference import adapt_zenodo_pm_reference_csv
from iiot_ai_sensor_gateway.decision import build_sensor_decision
from iiot_ai_sensor_gateway.edge_forecasting import (
    evaluate_edge_forecast,
    load_edge_model,
    predict_edge_forecast,
    train_edge_forecast,
)
from iiot_ai_sensor_gateway.forecasting import TARGET_NAMES
from iiot_ai_sensor_gateway.streaming_detection import (
    RiverDependencyError,
    StreamingConfig,
    StreamingDetectionPipeline,
    build_native_pipeline,
    build_river_pipeline,
    run_streaming_detection,
)

HAS_NUMPY = importlib.util.find_spec("numpy") is not None
HAS_TORCH = importlib.util.find_spec("torch") is not None
HAS_ML = HAS_NUMPY and HAS_TORCH
HAS_RIVER = importlib.util.find_spec("river") is not None


class DatasetAdapterTests(unittest.TestCase):
    def test_uci_adapter_preserves_reference_units_and_missing_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            source = temp / "AirQualityUCI.csv"
            output = temp / "uci.jsonl"
            source.write_text(
                "Date;Time;CO(GT);PT08.S1(CO);NOx(GT);PT08.S3(NOx);NO2(GT);PT08.S4(NO2);PT08.S5(O3);T;RH;AH;;\n"
                "10/03/2004;18.00.00;2,6;1360;166;1056;113;1692;1268;13,6;48,9;0,7578;;\n"
                "10/03/2004;19.00.00;-200;1292;-200;1174;92;1559;972;13,3;47,7;0,7255;;\n",
                encoding="utf-8",
            )
            stats = adapt_uci_air_quality_csv(source, output)
            records = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(stats.written_rows, 2)
            self.assertEqual(records[0]["sensor"]["temperature_c"], 13.6)
            self.assertIsNone(records[0]["sensor"]["co_ppm"])
            self.assertEqual(records[0]["reference"]["co_mg_m3"], 2.6)
            self.assertIsNone(records[1]["reference"]["co_mg_m3"])
            self.assertIsNone(records[0]["sensor"]["o3_ppm"])

    def test_bristol_adapter_keeps_gas_as_iaq_index(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            source = temp / "device.csv"
            output = temp / "bristol.jsonl"
            source.write_text(
                "Time,DeviceId,Sensor,Value\n"
                "1643673600,A,Temperature,22.5\n"
                "1643673600,A,Gas,87\n"
                "1643673600,A,Pressure,1009.2\n",
                encoding="utf-8",
            )
            stats = adapt_bristol_bme680_csv(source, output)
            records = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(stats.written_rows, 3)
            gas = records[1]
            self.assertEqual(gas["reference"]["bme680_iaq_index"], 87.0)
            self.assertIsNone(gas["sensor"]["bme_gas_ohm"])
            self.assertEqual(records[0]["sensor"]["temperature_c"], 22.5)

    def test_zenodo_pm_adapter_keeps_reference_separate_from_project_sensor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            source = temp / "df_pm_2min.csv"
            output = temp / "pm_reference.jsonl"
            source.write_text(
                '"","datetime_cut","PM2.5","PM10","PMtot","PM1","date"\n'
                '"1",2020-12-14 00:00:00,11.365,19.84,21.245,4.275,2020-12-14 00:00:00\n'
                '"2",2020-12-14 00:02:00,-1,13.53,14.56,4.43,2020-12-14 00:02:00\n',
                encoding="utf-8",
            )
            stats = adapt_zenodo_pm_reference_csv(source, output)
            records = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(stats.written_rows, 2)
            self.assertEqual(records[0]["reference"]["instrument"], "Fidas 200S")
            self.assertEqual(records[0]["reference"]["pm25_ug_m3"], 11.365)
            self.assertIsNone(records[0]["sensor"]["pm25_ug_m3"])
            self.assertIsNone(records[1]["reference"]["pm25_ug_m3"])
            self.assertEqual(
                records[0]["provenance"]["source_doi"], "10.5281/zenodo.7198378"
            )

    def test_dataset_catalog_is_valid_and_all_missing_are_explicit(self):
        catalog = json.loads((ROOT / "datasets/catalog.json").read_text())
        self.assertEqual(catalog["schema_version"], "iiot.dataset_catalog.v1")
        ids = {item["id"] for item in catalog["datasets"]}
        self.assertEqual(len(ids), len(catalog["datasets"]))
        self.assertIn("uci_air_quality_360", ids)
        self.assertIn("bristol_smart_building_bme680", ids)
        self.assertIn("zenodo_fidas_pm_reference_7198378", ids)
        self.assertIn("senseurcity_multicity_17858205", ids)
        for item in catalog["datasets"]:
            self.assertIn("license", item)
            self.assertIn("quality_notes", item)
            self.assertIn("explicitly_missing", item)
            if item.get("download_mode") == "script_allowed":
                self.assertRegex(item.get("sha256", ""), r"^[0-9a-f]{64}$")


class FakeAnomaly:
    def __init__(self):
        self.learned = 0

    def score_one(self, features):
        return features["x"]

    def learn_one(self, features):
        self.learned += 1
        return self


class FakeDrift:
    def __init__(self):
        self.drift_detected = False

    def update(self, value):
        self.drift_detected = value > 0.8
        return self


class StreamingAndDecisionTests(unittest.TestCase):
    def test_streaming_pipeline_warmup_anomaly_and_drift(self):
        pipeline = StreamingDetectionPipeline(
            FakeAnomaly(), {"x": FakeDrift()}, StreamingConfig(warmup_samples=2, anomaly_threshold=0.7)
        )
        first = pipeline.process({"x": 0.2}, timestamp="2026-07-10T00:00:00Z")
        second = pipeline.process({"x": 0.9}, timestamp="2026-07-10T00:01:00Z")
        self.assertFalse(first["warmup_complete"])
        self.assertTrue(second["warmup_complete"])
        self.assertTrue(second["is_anomaly"])
        self.assertTrue(second["drift_detected"])

    def test_streaming_pipeline_rejects_unscaled_features(self):
        pipeline = StreamingDetectionPipeline(FakeAnomaly(), {"x": FakeDrift()})
        with self.assertRaisesRegex(ValueError, "normalized"):
            pipeline.process({"x": 1.5})

    def test_native_streaming_backend_runs_end_to_end(self):
        pipeline = build_native_pipeline(
            feature_names=("x",),
            warmup_samples=4,
            anomaly_threshold=0.5,
            page_hinkley_threshold=0.05,
        )
        results = [pipeline.process({"x": 0.2}) for _ in range(8)]
        results.append(pipeline.process({"x": 0.95}))
        self.assertTrue(results[-1]["warmup_complete"])
        self.assertGreaterEqual(results[-1]["anomaly_score"], 0.0)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            source = temp / "features.jsonl"
            output = temp / "events.jsonl"
            rows = [
                {"timestamp": f"2026-07-10T00:{index:02d}:00Z", "features": {"x": value}}
                for index, value in enumerate([0.2, 0.21, 0.19, 0.2, 0.22, 0.95])
            ]
            source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            summary = run_streaming_detection(
                source,
                output,
                feature_names=("x",),
                backend="native",
                warmup_samples=3,
                anomaly_threshold=0.5,
                page_hinkley_threshold=0.05,
            )
            self.assertEqual(summary["processed"], 6)
            self.assertEqual(summary["backend"], "native.RobustZScore+PageHinkley")
            self.assertTrue(output.exists())
            self.assertTrue(output.with_suffix(output.suffix + ".meta.json").exists())

    @unittest.skipIf(HAS_RIVER, "only verifies dependency failure when River is absent")
    def test_river_dependency_error_is_actionable(self):
        with self.assertRaisesRegex(RiverDependencyError, "pip install"):
            build_river_pipeline(feature_names=("x",))

    def test_decision_abstains_on_invalid_data_and_keeps_no2_ordinal(self):
        invalid = build_sensor_decision(sensor={}, quality="invalid", source_status="sensor_error")
        self.assertTrue(invalid["abstain"])
        self.assertEqual(invalid["env_status"], "unknown")
        normal = build_sensor_decision(
            sensor={
                "temperature_c": 28,
                "humidity_pct": 60,
                "co_ppm": 2,
                "o3_ppm": 0.03,
                "co2_ppm": 650,
                "pm25_ug_m3": 12,
                "battery_voltage": 4.0,
                "no2_ratio": 1.1,
            },
            quality="valid",
            source_status="ok",
            anomaly_score=0.2,
        )
        self.assertFalse(normal["abstain"])
        self.assertEqual(normal["env_status"], "normal")
        self.assertEqual(normal["no2_semantics"], "ordinal_ratio_only")


@unittest.skipUnless(HAS_ML, "NumPy and PyTorch are optional")
class EdgeForecastTests(unittest.TestCase):
    def _dataset(self, path: Path):
        import numpy as np

        rng = np.random.default_rng(42)
        sequence_length = 12
        target_count = len(TARGET_NAMES)
        feature_names = np.asarray(TARGET_NAMES)
        target_indices = np.arange(target_count, dtype=np.int64)

        def make(samples):
            base = rng.uniform(0.1, 0.8, size=(samples, sequence_length, target_count)).astype("float32")
            y = (base[:, -1, :] * 0.8 + base[:, -2, :] * 0.2).astype("float32")
            return base, y

        X_train, y_train = make(40)
        X_val, y_val = make(12)
        X_test, y_test = make(12)
        np.savez_compressed(
            path,
            X_train=X_train,
            y_train=y_train,
            X_val=X_val,
            y_val=y_val,
            X_test=X_test,
            y_test=y_test,
            feature_names=feature_names,
            target_names=np.asarray(TARGET_NAMES),
            target_indices=target_indices,
            normalization_ranges_json=np.asarray([json.dumps({name: [0, 1] for name in TARGET_NAMES})]),
            dataset_meta_json=np.asarray([json.dumps({"lane": "unit_test"})]),
        )

    def test_fits_and_dlinear_train_evaluate_predict(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            dataset = temp / "dataset.npz"
            self._dataset(dataset)
            for model_type in ("fits", "dlinear"):
                output_dir = temp / model_type
                training = train_edge_forecast(
                    dataset,
                    output_dir,
                    model_type=model_type,
                    epochs=2,
                    batch_size=8,
                    patience=2,
                    device="cpu",
                )
                self.assertEqual(training["status"], "EXPERIMENTAL")
                self.assertGreater(training["parameter_count"], 0)
                self.assertIsNone(training["resource_measurement"]["raspberry_pi_claim"])
                metrics = evaluate_edge_forecast(
                    dataset, training["model_path"], output_dir, seasonal_period=2, device="cpu"
                )
                self.assertIn(metrics["model_readiness"], {"PROMISING", "EXPERIMENTAL"})
                self.assertIn(
                    metrics["baseline_comparison_status"],
                    {"BEATS_BASELINE", "UNDER_BASELINE", "MIXED"},
                )
                self.assertIn(metrics["data_status"], {"PASS", "WARN", "FAIL", "UNKNOWN"})
                self.assertIn(metrics["status"], {"PASS", "FAIL_OR_EXPERIMENTAL"})
                self.assertIn("last_value", metrics["splits"]["test"]["baselines"])
                self.assertIn("seasonal_naive", metrics["splits"]["test"]["baselines"])
                predictions = predict_edge_forecast(
                    dataset,
                    training["model_path"],
                    output_dir / "predictions.jsonl",
                    max_samples=3,
                    device="cpu",
                )
                self.assertEqual(len(predictions.read_text().splitlines()), 3)
                _model, checkpoint, _device = load_edge_model(training["model_path"], "cpu")
                self.assertEqual(checkpoint["model_type"], model_type)


if __name__ == "__main__":
    unittest.main()
