#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark a checksummed forecast manifest v2 on the current host"
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--split", choices=("train", "val", "test"), default="test")
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--parity-samples", type=int, default=32)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from iiot_ai_sensor_gateway.runtime_benchmark import benchmark_forecast_runtime_v2

    result = benchmark_forecast_runtime_v2(
        args.dataset,
        args.manifest,
        args.output,
        split=args.split,
        iterations=args.iterations,
        warmup=args.warmup,
        device=args.device,
        parity_samples=args.parity_samples,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
