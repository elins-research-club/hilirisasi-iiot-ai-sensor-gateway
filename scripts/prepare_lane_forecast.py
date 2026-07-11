#!/usr/bin/env python3
"""Prepare a lane-local forecast NPZ from window JSONL.

Windows-safe helper used by laptop_full_model_bakeoff.ps1 (avoids fragile py -c quoting).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iiot_ai_sensor_gateway.forecasting import prepare_forecast_dataset  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows", required=True)
    parser.add_argument("--output-npz", required=True)
    parser.add_argument("--output-meta", required=True)
    parser.add_argument("--targets", required=True, help="comma-separated target feature names")
    parser.add_argument("--horizon-steps", type=int, default=5)
    parser.add_argument("--window-size", type=int, default=12)
    parser.add_argument("--purge-gap-steps", type=int, default=5)
    parser.add_argument(
        "--cadence-sec",
        type=float,
        default=0.0,
        help="0=infer from timestamps; positive value is validated against timestamps",
    )
    parser.add_argument("--cadence-relative-tolerance", type=float, default=0.10)
    parser.add_argument("--max-irregular-fraction", type=float, default=0.05)
    args = parser.parse_args(argv)

    targets = tuple(item.strip() for item in args.targets.split(",") if item.strip())
    if not targets:
        raise SystemExit("targets must be non-empty")

    stats = prepare_forecast_dataset(
        args.windows,
        args.output_npz,
        args.output_meta,
        horizon_steps=args.horizon_steps,
        window_size=args.window_size,
        purge_gap_steps=args.purge_gap_steps,
        target_names=targets,
        resample_interval_sec=args.cadence_sec if args.cadence_sec > 0 else None,
        cadence_relative_tolerance=args.cadence_relative_tolerance,
        max_irregular_fraction=args.max_irregular_fraction,
    )
    print(json.dumps(stats.as_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
