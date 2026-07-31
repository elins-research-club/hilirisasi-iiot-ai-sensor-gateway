#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from iiot_ai_sensor_gateway.v3d_real_data import (  # noqa: E402
    DATASET_CONFIGS,
    prepare_dataset,
    summarize_runs,
    train_and_evaluate,
)


def csv_values(value: str) -> tuple[str, ...]:
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    if not items:
        raise argparse.ArgumentTypeError("expected comma-separated values")
    return items


def csv_ints(value: str) -> tuple[int, ...]:
    try:
        values = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected comma-separated integers") from exc
    if not values or any(item < 0 for item in values):
        raise argparse.ArgumentTypeError("seeds must be non-negative integers")
    return values


def prepared_path(prepared_root: Path, key: str) -> Path:
    return prepared_root / key / "forecast_v3d.npz"


def metadata_path(prepared_root: Path, key: str) -> Path:
    return prepared_root / key / "metadata_v3d.json"


def prepare(args: argparse.Namespace) -> None:
    for key in args.datasets:
        if key not in DATASET_CONFIGS:
            raise SystemExit(f"unknown dataset key: {key}")
        config = DATASET_CONFIGS[key]
        raw = args.raw_dir / config.filename
        if not raw.exists():
            raise SystemExit(f"missing raw dataset: {raw}")
        metadata = prepare_dataset(
            key,
            raw,
            prepared_path(args.prepared_dir, key),
            metadata_path(args.prepared_dir, key),
        )
        print(json.dumps({"prepared": key, "window_counts": metadata["window_counts"], "accepted_series": len(metadata["accepted_series"])}))


def benchmark(args: argparse.Namespace) -> None:
    for key in args.datasets:
        dataset = prepared_path(args.prepared_dir, key)
        if not dataset.exists():
            raise SystemExit(f"missing prepared dataset: {dataset}")
        for model in args.models:
            for seed in args.seeds:
                output = args.output_dir / key / model / f"seed_{seed}"
                run_path = output / "run.json"
                if run_path.exists() and not args.force:
                    print(f"SKIP {key}/{model}/seed_{seed}: run.json exists")
                    continue
                print(f"RUN {key}/{model}/seed_{seed}", flush=True)
                result = train_and_evaluate(
                    dataset,
                    output,
                    model,
                    seed,
                    epochs=args.epochs,
                    patience=args.patience,
                    batch_size=args.batch_size,
                    device=args.device,
                )
                print(
                    json.dumps(
                        {
                            "dataset": key,
                            "model": model,
                            "seed": seed,
                            "mean_mase": result["metrics"]["mean_mase"],
                            "mean_skill": result["metrics"]["mean_rmse_skill"],
                            "passed": result["baseline_gate_passed"],
                            "seconds": result["training_seconds"],
                        }
                    ),
                    flush=True,
                )
    summary = summarize_runs(args.output_dir, args.summary_dir)
    print(json.dumps({"run_count": summary["run_count"], "summary": summary["summary_csv"]}))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="V3D real-data forecasting benchmark")
    parser.add_argument("command", choices=("prepare", "benchmark", "all", "summarize"))
    parser.add_argument("--raw-dir", type=Path, default=ROOT / "data" / "external" / "downloads")
    parser.add_argument("--prepared-dir", type=Path, default=ROOT / "data" / "v3d" / "prepared")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "models" / "v3d")
    parser.add_argument("--summary-dir", type=Path, default=ROOT / "data" / "v3d" / "results")
    parser.add_argument("--datasets", type=csv_values, default=("uci", "beijing", "intel"))
    parser.add_argument("--models", type=csv_values, default=("dlinear", "fits", "lstm", "patchtst"))
    parser.add_argument("--seeds", type=csv_ints, default=(42, 43, 44, 45, 46))
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--patience", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.epochs < 1 or args.patience < 1 or args.batch_size < 1:
        raise SystemExit("epochs, patience, and batch size must be positive")
    unknown_models = sorted(set(args.models).difference({"dlinear", "fits", "lstm", "patchtst"}))
    if unknown_models:
        raise SystemExit(f"unknown models: {unknown_models}")
    if args.command in {"prepare", "all"}:
        prepare(args)
    if args.command in {"benchmark", "all"}:
        benchmark(args)
    if args.command == "summarize":
        summary = summarize_runs(args.output_dir, args.summary_dir)
        print(json.dumps({"run_count": summary["run_count"], "summary": summary["summary_csv"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
