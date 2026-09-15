#!/usr/bin/env python3
"""Collect Pi 5 training/eval results into a single summary JSON + text table.

Usage: python3 scripts/collect_pi5_summary.py <models_root> [--out summary.json]
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="models root (e.g. models/pi5)")
    ap.add_argument("--out", default=None, help="output json path (default <root>/pi5_summary.json)")
    args = ap.parse_args()
    root = Path(args.root)
    rows = []
    for tr in sorted(glob.glob(str(root / "*" / "*" / "training.json"))):
        rel = os.path.relpath(tr, root).split(os.sep)
        lane, model = rel[0], rel[1]
        if lane not in SENSOR_LANES:
            continue
        d = json.load(open(tr))
        rm = d.get("resource_measurement", {}) or {}
        eval_path = Path(tr).parent / "eval_results"
        eval_metrics = None
        for cand in ("metrics.json", "evaluation.json", "eval_metrics.json"):
            p = eval_path / cand
            if p.exists():
                try:
                    eval_metrics = json.load(open(p))
                except Exception:
                    pass
                break
        rows.append({
            "lane": lane,
            "model": model,
            "train_wall_ms": round(float(rm.get("training_wall_ms") or 0.0), 1),
            "rss_kib": rm.get("process_max_rss_kib"),
            "params": d.get("param_count"),
            "best_val_loss": d.get("best_val_loss"),
            "epochs_ran": d.get("epochs_ran"),
            "hardware_label": rm.get("hardware_label"),
            "eval": eval_metrics,
        })
    rows.sort(key=lambda r: (r["lane"], r["model"]))
    out = Path(args.out) if args.out else root / "pi5_summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    # text table
    print(f"{'lane':>18} {'model':>14} {'wall_ms':>10} {'rss_kib':>9} {'params':>7} {'val_loss':>10} {'epochs':>6}")
    for r in rows:
        print(f"{r['lane']:>18} {r['model']:>14} {r['train_wall_ms']:>10.1f} {str(r['rss_kib']):>9} {str(r['params']):>7} {str(r['best_val_loss']):>10} {str(r['epochs_ran']):>6}")
    print(f"\nsaved {len(rows)} rows -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
