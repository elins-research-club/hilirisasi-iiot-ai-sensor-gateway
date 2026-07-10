from __future__ import annotations

from .buffer import NodeBuffer
from .config import AppConfig
from .contracts import WindowSample
from .features import extract_features
from .normalization import MinMaxNormalizer
from .parser import PayloadParser
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
        self.buffer = NodeBuffer(config.pipeline.buffer_max_size)
        self.normalizer = MinMaxNormalizer(config.normalization_ranges)
        self.window_builder = WindowBuilder(config.pipeline.window_size, config.pipeline.window_step)
        self.processed_features: set[tuple[str, str]] = set()

    def process_payload(self, payload: str | bytes | dict) -> list[WindowSample]:
        reading = self.parser.parse(payload)
        result = self.validator.validate(reading)
        self.buffer.add(result)
        points = resample(
            self.buffer.node_items(reading.node_id),
            self.config.pipeline.resample_interval_sec,
        )
        eligible_points = [
            point
            for point in points
            if point.valid_ratio >= self.config.pipeline.min_valid_ratio
        ]
        windows: list[WindowSample] = []
        for vector in extract_features(eligible_points):
            key = (vector.node_id, vector.timestamp.isoformat())
            if key in self.processed_features:
                continue
            self.processed_features.add(key)
            window = self.window_builder.add(self.normalizer.normalize(vector))
            if window is not None:
                windows.append(window)
        return windows
