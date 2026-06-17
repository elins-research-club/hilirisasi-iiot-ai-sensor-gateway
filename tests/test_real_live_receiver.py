import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from iiot_ai_sensor_gateway.config import load_config
from iiot_ai_sensor_gateway.parser import PayloadParser
from iiot_ai_sensor_gateway.real import FileReplaySource, LiveReceiver
from iiot_ai_sensor_gateway.validation import ReadingValidator


FIXTURE = ROOT / 'tests' / 'fixtures' / 'real_payload_samples.jsonl'


def _receiver() -> LiveReceiver:
    config = load_config(ROOT / 'config/default.toml')
    parser = PayloadParser(config.identity.gateway_id, config.identity.default_room_id)
    validator = ReadingValidator(config.validation_ranges, config.pipeline.sequence_gap_warn)
    return LiveReceiver(parser, validator)


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


class RealLiveReceiverTests(unittest.TestCase):
    def test_replay_file_writes_accepted_rejected_and_events(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            summary = _receiver().run(FileReplaySource(FIXTURE), output_dir, max_messages=0)

            accepted = _read_jsonl(output_dir / 'accepted_payloads.jsonl')
            rejected = _read_jsonl(output_dir / 'rejected_payloads.jsonl')
            events = _read_jsonl(output_dir / 'receiver_events.jsonl')

            self.assertEqual(summary.processed_count, 8)
            self.assertEqual(summary.accepted_count, 4)
            self.assertEqual(summary.rejected_count, 4)
            self.assertEqual(len(accepted), 4)
            self.assertEqual(len(rejected), 4)
            self.assertEqual(events[0]['event'], 'receiver_started')
            self.assertEqual(events[-1]['event'], 'receiver_stopped')
            self.assertEqual(accepted[0]['source'], 'replay_file')
            self.assertIn('payload_original_compact_json', accepted[0])

    def test_invalid_samples_are_rejected_with_categories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            _receiver().run(FileReplaySource(FIXTURE), output_dir)

            rejected = _read_jsonl(output_dir / 'rejected_payloads.jsonl')
            categories = {row['category'] for row in rejected}
            issues = {issue for row in rejected for issue in row.get('validation_issues', [])}

            self.assertEqual(categories, {'validation_error'})
            self.assertIn('sensor_error', issues)
            self.assertIn('range_co_raw', issues)
            self.assertIn('range_bme_gas_raw', issues)
            self.assertIn('missing_node_id', issues)

    def test_max_messages_limits_replay_processing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            summary = _receiver().run(FileReplaySource(FIXTURE), output_dir, max_messages=2)

            accepted = _read_jsonl(output_dir / 'accepted_payloads.jsonl')
            rejected = _read_jsonl(output_dir / 'rejected_payloads.jsonl')

            self.assertEqual(summary.processed_count, 2)
            self.assertEqual(summary.accepted_count, 2)
            self.assertEqual(summary.rejected_count, 0)
            self.assertEqual(len(accepted), 2)
            self.assertEqual(rejected, [])

    def test_wrapped_payload_metadata_stays_outside_parser_core(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            _receiver().run(FileReplaySource(FIXTURE), output_dir)

            accepted = _read_jsonl(output_dir / 'accepted_payloads.jsonl')
            wrapped = next(row for row in accepted if row['node_id'] == 'esp32c6_node_02')

            self.assertEqual(wrapped['source'], 'replay_file')
            self.assertEqual(wrapped['sequence'], 1)
            self.assertIn('raw_payload', wrapped['raw_line'])
            self.assertNotIn('raw_payload', wrapped['payload'])


if __name__ == '__main__':
    unittest.main()
