import json
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from iiot_ai_sensor_gateway.parser import PayloadParser
from iiot_ai_sensor_gateway.validation import ReadingValidator


FIXTURE = ROOT / 'tests' / 'fixtures' / 'real_payload_samples.jsonl'


def _load_samples():
    return [json.loads(line) for line in FIXTURE.read_text(encoding='utf-8').splitlines() if line.strip()]


class RealPayloadContractTests(unittest.TestCase):
    def test_fixture_payloads_parse_with_existing_parser(self):
        parser = PayloadParser('raspi_gateway_01', 'lab_default')
        samples = _load_samples()
        self.assertGreaterEqual(len(samples), 7)

        for sample in samples:
            payload = sample['payload']
            reading = parser.parse(payload)
            self.assertEqual(reading.gateway_id, payload.get('gw', 'raspi_gateway_01'))
            self.assertEqual(reading.room_id, payload.get('r', 'lab_default'))
            self.assertIsNotNone(reading.timestamp)
            self.assertIsNotNone(reading.sensor.temperature_c)
            self.assertIsNotNone(reading.sensor.humidity_pct)
            self.assertTrue(
                any(
                    value is not None
                    for value in (
                        reading.sensor.bme_gas_raw,
                        reading.sensor.co_raw,
                        reading.sensor.voc_raw,
                        reading.sensor.gas_raw,
                    )
                )
            )

    def test_real_like_payload_validation_matches_contract_expectations(self):
        parser = PayloadParser('raspi_gateway_01', 'lab_default')
        validator = ReadingValidator(
            {
                'temperature_c': (-10, 80),
                'humidity_pct': (0, 100),
                'pressure_hpa': (900, 1100),
                'bme_gas_raw': (0, 5000),
                'co_raw': (0, 0.05),
            }
        )

        results = {}
        for sample in _load_samples():
            reading = parser.parse(sample['payload'])
            result = validator.validate(reading)
            results[sample['case']] = result
            self.assertEqual(result.is_valid, sample['expected_valid'], sample['case'])
            for issue in sample.get('expected_issues', []):
                self.assertIn(issue, result.issues, sample['case'])

        self.assertIn('missing_pressure_hpa', results['missing_optional_pressure'].issues)
        self.assertIn('sensor_error', results['sensor_error'].issues)
        self.assertIn('sequence_gap', results['sequence_gap'].issues)
        self.assertIn('range_co_raw', results['high_co'].issues)
        self.assertIn('range_bme_gas_raw', results['high_gas'].issues)

    def test_receiver_wrapped_payload_keeps_raw_metadata_outside_parser(self):
        parser = PayloadParser('raspi_gateway_01', 'lab_default')
        wrapped = next(sample for sample in _load_samples() if sample['case'] == 'receiver_wrapped_payload')

        reading = parser.parse(wrapped['payload'])

        self.assertEqual(wrapped['receiver_id'], 'raspi_gateway_01_lora_rx')
        self.assertIn('raw_payload', wrapped)
        self.assertEqual(reading.node_id, 'esp32c6_node_02')
        self.assertEqual(reading.radio.rssi, None)


if __name__ == '__main__':
    unittest.main()
