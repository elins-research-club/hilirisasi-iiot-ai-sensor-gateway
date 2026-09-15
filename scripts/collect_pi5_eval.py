#!/usr/bin/env python3
"""Collect eval metrics.json summaries from Pi 5 models into one table.

Usage: python3 scripts/collect_pi5_eval.py <models_root> [--out <json>]
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
    ap.add_argument("root")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    root = Path(args.root)
    rows = []
    for metrics in sorted(glob.glob(str(root / "*" / "*" / "eval_results" / "metrics.json"))):
        rel = os.path.relpath(metrics, root).split(os.sep)
        lane, model = rel[0], rel[1]
        if lane not in SENSOR_LANES:
            continue
        try:
            d = json.loads(Path(metrics).read_text(encoding="utf-8"))
        except Exception:
            continue
        bg = d.get("baseline_gate", {}) or {}
        dq = d.get("data_quality", {}) or {}
        rows.append({
            "lane": lane,
            "model": model,
            "status": d.get("status"),
            "model_readiness": d.get("model_readiness"),
            "baseline_passed": bg.get("baseline_passed"),
            "data_quality_passed": bg.get("data_quality_passed"),
            "gate_passed": bg.get("passed"),
            "baseline_comparison": d.get("baseline_comparison_status"),
            "model_rmse": bg.get("model_rmse"),
            "baseline_rmse": bg.get("baseline_rmse"),
            "rmse_skill_score": bg.get("rmse_skill_score"),
            "data_status": d.get("data_status"),
            "blocked_targets": list((dq.get("blocked_targets") or {}).keys()),
        })
    rows.sort(key=lambda r: (r["lane"], r["model"]))
    out = Path(args.out) if args.out else root / "pi5_eval_summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"{'lane':>18} {'model':>14} {'status':>6} {'ready':>10} {'bl_pass':>7} {'dq_pass':>7} {'gate':>5} {'skill':>8}")
    for r in rows:
        skill = r["rmse_skill_score"]
        skill_s = f"{skill:.3f}" if skill is not None else "None"
        print(f"{r['lane']:>18} {r['model']:>14} {str(r['status']):>6} {str(r['model_readiness']):>10} "
              f"{str(r['baseline_passed']):>7} {str(r['data_quality_passed']):>7} {str(r['gate_passed']):>5} {skill_s:>8}")
    print(f"\nsaved {len(rows)} rows -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
