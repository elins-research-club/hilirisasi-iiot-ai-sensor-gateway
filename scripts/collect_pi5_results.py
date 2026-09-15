#!/usr/bin/env python3
"""Collect full Pi 5 results: training resource, inference latency, eval metrics.

Usage: python3 scripts/collect_pi5_results.py <models_root> [--out <json>]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

from pathlib import Path

SENSOR_LANES = {
    "data_bme",
    "data_mentah_bme688",
    "data_co2",
    "data_no2",
    "data_pms_1",
    "data_INA226",
}


def load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    root = Path(args.root)
    rows = []
    for model_pt_str in sorted(glob.glob(str(root / "*" / "*" / "model.pt"))):
        model_pt = Path(model_pt_str)
        rel = os.path.relpath(model_pt, root).split(os.sep)
        lane, model = rel[0], rel[1]
        if lane not in SENSOR_LANES:
            continue
        base = model_pt.parent
        train = load_json(base / "training.json") or {}
        bench = load_json(base / "benchmark.json") or {}
        eval_data = None
        for cand in ("eval_results/metrics.json", "eval_results/evaluation.json",
                     "eval_results/eval_metrics.json", "eval_results.json"):
            p = base / cand
            if p.exists():
                eval_data = load_json(p)
                if eval_data:
                    break
        rm = train.get("resource_measurement", {}) or {}
        lat = bench.get("latency_ms", {}) or {}
        row = {
            "lane": lane,
            "model": model,
            "train_wall_ms": round(float(rm.get("training_wall_ms") or 0.0), 1),
            "train_rss_kib": rm.get("process_max_rss_kib"),
            "param_count": train.get("param_count"),
            "best_val_loss": train.get("best_val_loss"),
            "epochs_ran": train.get("epochs_ran"),
            "hardware_label": rm.get("hardware_label", "current_host"),
            "lat_mean_ms": round(float(lat.get("mean") or 0.0), 4),
            "lat_median_ms": round(float(lat.get("median") or 0.0), 4),
            "lat_p95_ms": round(float(lat.get("p95") or 0.0), 4),
            "throughput_sps": round(float(bench.get("throughput_samples_per_sec") or 0.0), 1),
            "bench_rss_kib": bench.get("process_max_rss_kib"),
            "eval": eval_data,
        }
        rows.append(row)
    rows.sort(key=lambda r: (r["lane"], r["model"]))
    out = Path(args.out) if args.out else root / "pi5_full_results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    hdr = (f"{'lane':>18} {'model':>14} {'train_ms':>9} {'rss_kib':>8} {'params':>6} "
           f"{'lat_mean':>9} {'lat_p95':>9} {'thr/s':>8}")
    print(hdr)
    for r in rows:
        print(f"{r['lane']:>18} {r['model']:>14} {r['train_wall_ms']:>9.1f} "
              f"{str(r['train_rss_kib']):>8} {str(r['param_count']):>6} "
              f"{r['lat_mean_ms']:>9.4f} {r['lat_p95_ms']:>9.4f} {r['throughput_sps']:>8.1f}")
    print(f"\nsaved {len(rows)} rows -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
