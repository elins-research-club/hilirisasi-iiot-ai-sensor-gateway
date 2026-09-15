#!/usr/bin/env python3
"""Benchmark inference latency per sample for edge (FITS/DLinear) and LSTM models.

Runs on the target host (e.g. Raspberry Pi 5) and writes a benchmark.json with
mean/median/p95 latency per sample, throughput (samples/s), and process RSS.

Usage:
    python3 scripts/benchmark_inference.py \
        --dataset <dataset.npz> \
        --model <model.pt> \
        --model-kind {edge,lstm} \
        --output <benchmark.json> \
        [--iterations 200] [--device cpu] [--warmup 10]

This is a *measurement* script: it loads the checkpoint, runs inference on the
test split (or synthetic windows when the split is empty), and reports stats.
No training, no writes to the model checkpoint.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path


def detect_hardware_label() -> str:
    """Return a conservative label for the host that produced the measurement."""
    override = os.environ.get("IIOT_HARDWARE_LABEL", "").strip()
    if override:
        return override
    try:
        model = Path("/proc/device-tree/model").read_text(
            encoding="utf-8", errors="ignore"
        ).strip("\x00\n")
    except OSError:
        model = ""
    if "raspberry pi 5" in model.lower():
        return "raspberry_pi_5"
    system = platform.system().strip().lower().replace("-", "_")
    machine = platform.machine().strip().lower().replace("-", "_")
    return f"{system}_{machine}" if system and machine else "unknown_host"

import numpy as np


def _resource_max_rss_kib():
    try:
        import resource

        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except ImportError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--model-kind", required=True, choices=["edge", "lstm"])
    ap.add_argument("--output", required=True)
    ap.add_argument("--iterations", type=int, default=200)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--warmup", type=int, default=10)
    ap.add_argument(
        "--hardware-label",
        default=None,
        help="explicit measurement label; otherwise detect the current host",
    )
    args = ap.parse_args()
    if args.iterations < 1:
        ap.error("--iterations must be positive")
    if args.warmup < 0:
        ap.error("--warmup must be non-negative")

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from iiot_ai_sensor_gateway import edge_forecasting as ef
    from iiot_ai_sensor_gateway import forecasting as fc

    torch = __import__("torch")

    data = ef._load_dataset(args.dataset)
    X_test = data["X_test"].astype("float32")
    feature_names = data["feature_names_tuple"]
    target_indices = data["target_indices_array"]
    n_feat = X_test.shape[-1]
    n_targets = len(target_indices)
    seq_len = X_test.shape[1]

    if args.model_kind == "edge":
        model, checkpoint, device = ef.load_edge_model(args.model, args.device)
        model_type = checkpoint.get("model_type", "unknown")
        # build the exact input the model expects: selected target history
        def make_input(i):
            return torch.from_numpy(
                ef._select_targets(X_test[i : i + 1], target_indices).astype("float32")
            ).to(device)

        def run_one(inp):
            return model(inp)
    else:
        model, checkpoint, device = fc._load_model(args.model, args.device)
        model_type = checkpoint.get("model_version", "unknown")
        input_size = int(checkpoint["input_size"])
        # LSTM expects (batch, seq, input_size); use full feature windows
        def make_input(i):
            return torch.from_numpy(X_test[i : i + 1]).to(device)

        def run_one(inp):
            return model(inp)

    model.eval()
    n = min(len(X_test), args.iterations) if len(X_test) else args.iterations
    if len(X_test) == 0:
        # synthetic windows when the split is empty (should not happen for our data)
        X_test = np.zeros((n, seq_len, n_feat), dtype="float32")

    # warmup
    with torch.no_grad():
        for i in range(min(args.warmup, n)):
            run_one(make_input(i))
    torch.cuda.synchronize() if torch.cuda.is_available() else None

    latencies = []
    rss_before = _resource_max_rss_kib()
    with torch.no_grad():
        for i in range(n):
            inp = make_input(i)
            t0 = time.perf_counter()
            _ = run_one(inp)
            latencies.append((time.perf_counter() - t0) * 1000.0)
    rss_after = _resource_max_rss_kib()

    lat = np.array(latencies)
    result = {
        "dataset": str(args.dataset),
        "model": str(args.model),
        "model_kind": args.model_kind,
        "model_type": model_type,
        "device": device,
        "samples_benchmarked": int(n),
        "window_size": int(X_test.shape[1]),
        "feature_count": int(n_feat),
        "target_count": int(n_targets),
        "latency_ms": {
            "mean": float(lat.mean()),
            "median": float(np.median(lat)),
            "p95": float(np.percentile(lat, 95)),
            "min": float(lat.min()),
            "max": float(lat.max()),
        },
        "throughput_samples_per_sec": float(1000.0 / lat.mean()),
        "process_max_rss_kib": rss_after or rss_before,
        "hardware_label": args.hardware_label or detect_hardware_label(),
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
