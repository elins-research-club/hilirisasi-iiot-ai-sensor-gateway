from __future__ import annotations

from .buffer import NodeBuffer
from .config import AppConfig
from .contracts import FeatureVector, SensorReading, ValidationResult, WindowSample
from .features import extract_features
from .normalization import MinMaxNormalizer
from .parser import PayloadParser
from .preprocessing import GatewaySemanticPreprocessor
from .resampling import resample
from .validation import ReadingValidator
from .windowing import WindowBuilder

class PreModelPipeline:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.parser = PayloadParser(
            config.identity.gateway_id,
            config.identity.default_room_id,
            allow_legacy_v1=config.contract.allow_legacy_v1,
        )
        self.validator = ReadingValidator(
            config.validation_ranges,
            config.pipeline.sequence_gap_warn,
            config.pipeline.validation_state_max_entries,
        )
        self.preprocessor = GatewaySemanticPreprocessor(
            version=config.preprocessing.version,
            filters=config.preprocessing.filters,
            apply_to_v2=config.preprocessing.apply_to_v2,
            state_max_entries=config.preprocessing.state_max_entries,
        )
        self.buffer = NodeBuffer(config.pipeline.buffer_max_size)
        self.normalizer = MinMaxNormalizer(config.normalization_ranges)
        self.window_builder = WindowBuilder(config.pipeline.window_size, config.pipeline.window_step)
        self.processed_features: set[tuple[str, str, str, str]] = set()

    def process_payload(self, payload: str | bytes | dict) -> list[WindowSample]:
        reading = self.parser.parse(payload)
        _result, windows = self.process_reading(reading)
        return windows

    def process_reading(
        self, reading: SensorReading
    ) -> tuple[ValidationResult, list[WindowSample]]:
        """Process an already-adapted reading through the shared gateway core."""

        result, windows, _new_vectors = self.process_reading_detailed(reading)
        return result, windows

    def process_reading_detailed(
        self, reading: SensorReading
    ) -> tuple[ValidationResult, list[WindowSample], list[FeatureVector]]:
        """Process a reading and return only newly emitted feature vectors.

        This is used by the live model runtime so inference follows the same
        identity-isolated resampling path as the canonical pre-model pipeline.
        """

        result = self.preprocessor.process(self.validator.validate(reading))
        if not result.is_valid:
            return result, [], []
        self.buffer.add(result)
        points = resample(
            self.buffer.items_for(reading),
            self.config.pipeline.resample_interval_sec,
        )
        eligible_points = [
            point
            for point in points
            if point.valid_ratio >= self.config.pipeline.min_valid_ratio
        ]
        windows: list[WindowSample] = []
        new_vectors: list[FeatureVector] = []
        for vector in extract_features(eligible_points):
            key = (
                vector.gateway_id,
                vector.node_id,
                vector.room_id,
                vector.timestamp.isoformat(),
            )
            if key in self.processed_features:
                continue
            self.processed_features.add(key)
            new_vectors.append(vector)
            window = self.window_builder.add(self.normalizer.normalize(vector))
            if window is not None:
                windows.append(window)
        return result, windows, new_vectors
