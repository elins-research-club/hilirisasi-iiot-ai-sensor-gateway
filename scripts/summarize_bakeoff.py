#!/usr/bin/env python3
"""Build an atomic, repeated-seed-aware bake-off summary.

Supports edge and LSTM metric layouts. Artifacts remain EXPERIMENTAL and local;
this summary is not Raspberry Pi or production evidence.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def summarize_metrics(data: dict[str, Any], metrics_path: Path) -> dict[str, Any]:
    gate = data.get("baseline_gate")
    if isinstance(gate, dict):
        if gate.get("baseline_passed") or (
            gate.get("passed") and gate.get("rmse_skill_score") is not None and float(gate.get("rmse_skill_score") or 0) > 0
        ):
            baseline_status = "BEATS_BASELINE"
        elif gate.get("model_rmse") is not None and gate.get("baseline_rmse") is not None and float(gate["model_rmse"]) < float(gate["baseline_rmse"]):
            baseline_status = "MIXED"
        else:
            baseline_status = data.get("baseline_comparison_status") or "UNDER_BASELINE"
        return {
            "readiness": data.get("model_readiness"),
            "baseline_status": baseline_status,
            "skill": gate.get("rmse_skill_score"),
            "passed": bool(gate.get("passed")),
            "baseline_passed": bool(gate.get("baseline_passed", gate.get("passed"))),
            "data_quality_passed": bool(gate.get("data_quality_passed", True)),
            "test_rmse": gate.get("model_rmse"),
            "wins": f"{gate.get('per_target_wins')}/{gate.get('effective_target_count', gate.get('target_count'))}",
            "device": data.get("device"),
            "data_quality_status": (data.get("data_quality") or {}).get("status"),
            "model_type": data.get("model_type") or data.get("model_version"),
            "path": str(metrics_path),
        }

    test = (data.get("splits") or {}).get("test") or {}
    delta = test.get("baseline_delta") or {}
    per_target = delta.get("per_target") or {}
    wins = sum(bool(values.get("beats_baseline")) for values in per_target.values())
    count = len(per_target)
    status = data.get("baseline_comparison_status")
    return {
        "readiness": data.get("model_readiness"),
        "baseline_status": status,
        "skill": delta.get("overall_rmse_skill_score"),
        "passed": data.get("model_readiness") == "PROMISING",
        "baseline_passed": status == "BEATS_BASELINE",
        "data_quality_passed": bool(data.get("quality_gate_passed")),
        "test_rmse": (test.get("lstm") or {}).get("overall_rmse"),
        "wins": f"{wins}/{count}",
        "device": data.get("device"),
        "data_quality_status": (data.get("data_quality") or {}).get("status"),
        "path": str(metrics_path),
    }


def _numeric_summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean": None, "std": None, "min": None, "max": None}
    return {
        "count": len(values),
        "mean": statistics.fmean(values),
        "std": statistics.pstdev(values) if len(values) > 1 else 0.0,
        "min": min(values),
        "max": max(values),
    }


def build_summary(root: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "schema": "iiot.ai_sensor.bakeoff_summary.v2",
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "evidence_level": "EXPERIMENTAL_LAPTOP_OR_CURRENT_HOST",
        "raspberry_pi_claim": None,
        "lanes": {},
    }
    for lane_dir in sorted(root.glob("*")):
        if not lane_dir.is_dir() or lane_dir.name in {"smoke", "state"}:
            continue
        runs: dict[str, Any] = {}
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for metrics_path in sorted(lane_dir.rglob("metrics.json")):
            data = json.loads(metrics_path.read_text(encoding="utf-8-sig"))
            run_key = str(metrics_path.parent.relative_to(lane_dir)).replace("\\", "/")
            model_family = run_key.split("/", 1)[0]
            item = summarize_metrics(data, metrics_path)
            runs[run_key] = item
            grouped[model_family].append(item)
        aggregates: dict[str, Any] = {}
        for model_family, items in sorted(grouped.items()):
            skills = [float(item["skill"]) for item in items if item.get("skill") is not None]
            rmses = [float(item["test_rmse"]) for item in items if item.get("test_rmse") is not None]
            aggregates[model_family] = {
                "run_count": len(items),
                "skill": _numeric_summary(skills),
                "test_rmse": _numeric_summary(rmses),
                "readiness_counts": dict(Counter(str(item.get("readiness")) for item in items)),
                "all_quality_gates_passed": all(bool(item.get("data_quality_passed")) for item in items),
                "promotion_pass_count": sum(bool(item.get("passed")) for item in items),
            }
        summary["lanes"][lane_dir.name] = {
            "runs": runs,
            "aggregates": aggregates,
        }
    # Honest per-lane ranking over multi-seed means when available.
    rankings: dict[str, Any] = {}
    for lane_name, lane_info in summary["lanes"].items():
        scored = []
        for family, agg in (lane_info.get("aggregates") or {}).items():
            skill = (agg.get("skill") or {}).get("mean")
            if skill is None:
                continue
            scored.append(
                {
                    "family": family,
                    "skill_mean": skill,
                    "skill_std": (agg.get("skill") or {}).get("std"),
                    "test_rmse_mean": (agg.get("test_rmse") or {}).get("mean"),
                    "promotion_pass_count": agg.get("promotion_pass_count"),
                    "run_count": agg.get("run_count"),
                    "readiness_counts": agg.get("readiness_counts"),
                }
            )
        scored.sort(key=lambda item: float(item["skill_mean"]), reverse=True)
        rankings[lane_name] = {
            "ranked_families": scored,
            "best_family": scored[0]["family"] if scored else None,
            "evidence_level": "EXPERIMENTAL_PER_LANE_ONLY",
            "production_ready": False,
        }
    summary["lane_rankings"] = rankings
    summary["notes"] = [
        "Do not promote from proxy lanes (Gary/UCI/Fidas/sim) to production RAB hardware.",
        "Prefer multi-seed means over legacy single-run metrics.",
        "Host CUDA metrics are not Raspberry Pi latency/RSS evidence.",
    ]
    return summary


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary = Path(file.name)
            json.dump(data, file, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="models/bakeoff")
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)
    root = Path(args.root)
    output = Path(args.output) if args.output else root / "FULL_BAKEOFF_SUMMARY.json"
    summary = build_summary(root)
    atomic_write_json(output, summary)
    print(json.dumps(summary, indent=2))
    print("WROTE", output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
