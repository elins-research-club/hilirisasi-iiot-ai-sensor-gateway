#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


RUN_SCHEMA = "iiot.v3d.forecast_run.v1"
EXPECTED_DATASETS = frozenset(
    {
        "uci_air_quality_360",
        "beijing_multi_site_air_quality_501",
        "intel_lab_sensor_data",
    }
)
EXPECTED_MODELS = frozenset({"dlinear", "fits", "lstm", "patchtst"})
EXPECTED_SEEDS = frozenset({42, 43, 44, 45, 46})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_runs(root: Path) -> list[dict[str, Any]]:
    runs = []
    for path in sorted(root.rglob("run.json")):
        run = json.loads(path.read_text(encoding="utf-8"))
        run["run_path"] = str(path)
        run["run_sha256"] = sha256_file(path)
        runs.append(run)
    return runs


def validate_run_matrix(runs: list[dict[str, Any]]) -> dict[str, Any]:
    expected = {
        (dataset, model, seed)
        for dataset in EXPECTED_DATASETS
        for model in EXPECTED_MODELS
        for seed in EXPECTED_SEEDS
    }
    keys: list[tuple[str, str, int]] = []
    protocol_errors: list[str] = []
    metric_errors: list[str] = []
    prepared_hashes: dict[str, set[str]] = defaultdict(set)
    source_hashes: dict[str, set[str]] = defaultdict(set)

    for index, run in enumerate(runs):
        try:
            key = (str(run["dataset_id"]), str(run["model"]), int(run["seed"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"run {index} has an invalid dataset/model/seed identity") from exc
        keys.append(key)
        label = "/".join(map(str, key))
        if run.get("schema") != RUN_SCHEMA:
            protocol_errors.append(f"{label}: schema")
        if run.get("epochs_requested") != 12:
            protocol_errors.append(f"{label}: epochs_requested")
        if run.get("batch_size") != 256:
            protocol_errors.append(f"{label}: batch_size")

        prepared_hash = str(run.get("prepared_sha256", ""))
        source_hash = str(run.get("source_sha256", ""))
        if len(prepared_hash) != 64 or len(source_hash) != 64:
            protocol_errors.append(f"{label}: provenance_hash")
        prepared_hashes[key[0]].add(prepared_hash)
        source_hashes[key[0]].add(source_hash)

        metrics = run.get("metrics")
        if not isinstance(metrics, dict):
            metric_errors.append(f"{label}: metrics")
            continue
        for field in ("mean_mase", "mean_rmse_skill", "target_wins", "target_count"):
            value = metrics.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                metric_errors.append(f"{label}: {field}")
        per_target = metrics.get("per_target")
        if not isinstance(per_target, dict) or len(per_target) != metrics.get("target_count"):
            metric_errors.append(f"{label}: per_target_shape")
            continue
        for target, values in per_target.items():
            if not isinstance(values, dict):
                metric_errors.append(f"{label}/{target}: metrics")
                continue
            for field in ("mae", "rmse", "mase", "baseline_rmse", "rmse_skill"):
                value = values.get(field)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    metric_errors.append(f"{label}/{target}: {field}")

    counts = Counter(keys)
    actual = set(counts)
    duplicates = sorted(key for key, count in counts.items() if count != 1)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    inconsistent_prepared = sorted(dataset for dataset, values in prepared_hashes.items() if len(values) != 1)
    inconsistent_source = sorted(dataset for dataset, values in source_hashes.items() if len(values) != 1)
    if (
        len(runs) != len(expected)
        or duplicates
        or missing
        or extra
        or protocol_errors
        or metric_errors
        or inconsistent_prepared
        or inconsistent_source
    ):
        raise ValueError(
            "invalid V3D run matrix: "
            f"runs={len(runs)}, unique={len(actual)}, duplicates={duplicates[:3]}, "
            f"missing={missing[:3]}, extra={extra[:3]}, protocol={protocol_errors[:3]}, "
            f"metrics={metric_errors[:3]}, prepared_hashes={inconsistent_prepared}, "
            f"source_hashes={inconsistent_source}"
        )
    return {
        "run_count": len(runs),
        "unique_combinations": len(actual),
        "dataset_count": len(EXPECTED_DATASETS),
        "model_count": len(EXPECTED_MODELS),
        "seed_count": len(EXPECTED_SEEDS),
    }


def mean(values):
    return float(statistics.fmean(values))


def std(values):
    return float(statistics.pstdev(values))


def decision_for(dataset: str, model: str) -> tuple[str, str]:
    decisions = {
        ("uci_air_quality_360", "lstm"): (
            "PRIMARY_ACCURACY_CANDIDATE",
            "Mean MASE terendah; mengalahkan baseline pada 5/5 seed, tetapi jauh lebih berat daripada FITS.",
        ),
        ("uci_air_quality_360", "fits"): (
            "LIGHTWEIGHT_NEAR_ACCURACY_ALTERNATIVE",
            "Mean MASE hanya sedikit di atas LSTM, 5/5 seed lolos, dengan 66 parameter dan latency sangat rendah.",
        ),
        ("uci_air_quality_360", "dlinear"): (
            "CHALLENGER_UNSTABLE",
            "Hanya 3/5 seed lolos baseline dan variasi antarseed paling besar pada UCI.",
        ),
        ("uci_air_quality_360", "patchtst"): (
            "NOT_SELECTED_FOR_DATASET",
            "Rata-rata skill sedikit negatif dan hanya 2/5 seed lolos; kompleksitas tidak memberi nilai tambah yang konsisten.",
        ),
        ("beijing_multi_site_air_quality_501", "lstm"): (
            "PRIMARY_MASE_CANDIDATE",
            "Mean MASE terendah dan variasi antarseed paling kecil; 5/5 seed lolos baseline.",
        ),
        ("beijing_multi_site_air_quality_501", "dlinear"): (
            "LIGHTWEIGHT_HIGH_SKILL_ALTERNATIVE",
            "Skill terhadap baseline rata-rata tertinggi, 5/5 seed lolos, dan jauh lebih ringan; MASE sekitar 3,7% di atas LSTM.",
        ),
        ("beijing_multi_site_air_quality_501", "fits"): (
            "ULTRALIGHT_STABLE_ALTERNATIVE",
            "66 parameter, 5/5 seed lolos, tetapi akurasi agregat berada di bawah LSTM dan DLinear.",
        ),
        ("beijing_multi_site_air_quality_501", "patchtst"): (
            "RESEARCH_COMPARATOR",
            "5/5 seed lolos, tetapi tidak mengungguli LSTM dan tidak lebih ringan daripada model lain.",
        ),
        ("intel_lab_sensor_data", "patchtst"): (
            "PRIMARY_MASE_CHALLENGER",
            "Mean MASE terendah, tetapi hanya 4/5 seed lolos dan variasinya lebih besar daripada LSTM.",
        ),
        ("intel_lab_sensor_data", "lstm"): (
            "STABLE_SKILL_ALTERNATIVE",
            "Skill rata-rata lebih tinggi dan variasi MASE lebih kecil daripada PatchTST; 4/5 seed lolos, tetapi latency tertinggi.",
        ),
        ("intel_lab_sensor_data", "dlinear"): (
            "UNSTABLE_LIGHTWEIGHT_CHALLENGER",
            "Ringan, tetapi hanya 1/5 seed lolos baseline dan hasil berubah cukup besar antarseed.",
        ),
        ("intel_lab_sensor_data", "fits"): (
            "FAILED_BASELINE_GATE",
            "Tidak mengalahkan baseline pada satu pun seed; bukti negatif dipertahankan.",
        ),
    }
    return decisions[(dataset, model)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("models/v3d"))
    parser.add_argument("--summary", type=Path, default=Path("data/v3d/results/RESULT_SUMMARY_V3D.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/v3d/analysis"))
    args = parser.parse_args()

    runs = load_runs(args.root)
    try:
        matrix = validate_run_matrix(runs)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    args.output_dir.mkdir(parents=True, exist_ok=True)

    manifest_rows = []
    target_values: dict[tuple[str, str, str], dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for run in runs:
        metrics = run["metrics"]
        row = {
            "dataset_id": run["dataset_id"],
            "model": run["model"],
            "seed": run["seed"],
            "prepared_sha256": run["prepared_sha256"],
            "source_sha256": run["source_sha256"],
            "run_sha256": run["run_sha256"],
            "epochs_requested": run["epochs_requested"],
            "epochs_ran": run["epochs_ran"],
            "best_epoch": run["best_epoch"],
            "mean_mase": metrics["mean_mase"],
            "mean_rmse_skill": metrics["mean_rmse_skill"],
            "target_wins": metrics["target_wins"],
            "target_count": metrics["target_count"],
            "baseline_gate_passed": run["baseline_gate_passed"],
            "parameter_count": run["parameter_count"],
            "artifact_bytes": run["artifact_bytes"],
            "training_seconds": run["training_seconds"],
            "median_latency_ms_per_sample": run["latency"]["median_ms_per_sample"],
            "p95_latency_ms_per_sample": run["latency"]["p95_ms_per_sample"],
            "peak_process_rss_mb": run["peak_process_rss_mb"],
            "device": run["device"],
            "run_path": run["run_path"],
        }
        manifest_rows.append(row)
        for target, values in metrics["per_target"].items():
            key = (run["dataset_id"], run["model"], target)
            for field in ("mae", "rmse", "mase", "baseline_rmse", "rmse_skill"):
                value = values[field]
                if value is not None:
                    target_values[key][field].append(float(value))

    manifest_path = args.output_dir / "RUN_MANIFEST_V3D.csv"
    with manifest_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(manifest_rows[0]))
        writer.writeheader()
        writer.writerows(manifest_rows)

    summary_rows = list(csv.DictReader(args.summary.open(encoding="utf-8", newline="")))
    decision_rows = []
    for row in summary_rows:
        status, rationale = decision_for(row["dataset_id"], row["model"])
        decision_rows.append({**row, "decision_status": status, "rationale": rationale})
    decision_path = args.output_dir / "MODEL_DECISIONS_V3D.csv"
    with decision_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(decision_rows[0]))
        writer.writeheader()
        writer.writerows(decision_rows)

    target_rows = []
    for (dataset, model, target), fields in sorted(target_values.items()):
        target_rows.append(
            {
                "dataset_id": dataset,
                "model": model,
                "target": target,
                "seed_count": len(fields["rmse"]),
                "mean_mae": mean(fields["mae"]),
                "mean_rmse": mean(fields["rmse"]),
                "mean_mase": mean(fields["mase"]),
                "std_mase": std(fields["mase"]),
                "mean_baseline_rmse": mean(fields["baseline_rmse"]),
                "mean_rmse_skill": mean(fields["rmse_skill"]),
                "std_rmse_skill": std(fields["rmse_skill"]),
            }
        )
    target_path = args.output_dir / "TARGET_LEVEL_RESULTS_V3D.csv"
    with target_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(target_rows[0]))
        writer.writeheader()
        writer.writerows(target_rows)

    by_dataset: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in decision_rows:
        by_dataset[row["dataset_id"]].append(row)
    lines = [
        "# ANALISIS HASIL V3D - DATA NYATA DAN EVALUASI MODEL",
        "",
        "## Integritas eksperimen",
        "",
        "- 3 dataset nyata; 4 model; 5 seed; total 60 run.",
        "- Seluruh run memakai split kronologis, normalisasi train-only, dan complete-window primary analysis tanpa imputasi.",
        "- Baseline dipilih per target pada validation split lalu dikunci untuk test.",
        "- PatchTST yang diuji adalah adaptasi kompak channel-independent, bukan reproduksi bit-for-bit kode resmi.",
        "- Seluruh pengukuran resource berasal dari CPU VPS yang sama; bukan bukti Raspberry Pi, laptop RTX, energi, atau produksi.",
        "",
        "## Temuan utama",
        "",
        "1. Tidak ada model yang menang pada seluruh dataset dan seluruh tujuan.",
        "2. UCI: LSTM memberi mean MASE terbaik, tetapi FITS hanya sedikit tertinggal dengan 66 parameter dan latency puluhan kali lebih rendah.",
        "3. Beijing: LSTM paling baik menurut MASE dan paling stabil, sedangkan DLinear memberi skill baseline tertinggi dengan biaya jauh lebih kecil.",
        "4. Intel: PatchTST memberi mean MASE terbaik, tetapi LSTM lebih stabil dan mempunyai mean skill lebih tinggi; FITS gagal baseline pada 5/5 seed.",
        "5. Model modern bukan pemenang otomatis: PatchTST tidak terpilih pada UCI dan tidak mengungguli LSTM pada Beijing.",
        "",
    ]
    for dataset, rows in by_dataset.items():
        lines.extend([f"## {dataset}", "", "| Model | Mean MASE | SD MASE | Mean skill | Gate | Parameter | Latency ms/sampel | Keputusan |", "|---|---:|---:|---:|---:|---:|---:|---|"])
        for row in sorted(rows, key=lambda item: float(item["mean_mase"])):
            lines.append(
                "| {model} | {mean_mase:.4f} | {std_mase:.4f} | {skill:.4f} | {gate}/5 | {params} | {latency:.4f} | {status} |".format(
                    model=row["model"],
                    mean_mase=float(row["mean_mase"]),
                    std_mase=float(row["std_mase"]),
                    skill=float(row["mean_rmse_skill"]),
                    gate=row["baseline_gate_pass_count"],
                    params=row["parameter_count"],
                    latency=float(row["median_latency_ms_per_sample"]),
                    status=row["decision_status"],
                )
            )
        lines.append("")
        for row in rows:
            lines.append(f"- **{row['model']} - {row['decision_status']}:** {row['rationale']}")
        lines.append("")
    lines.extend(
        [
            "## Batas klaim",
            "",
            "- Hasil hanya berlaku pada dataset, target, horizon, split, implementasi, dan mesin ukur yang digunakan.",
            "- Data UCI dan Beijing adalah data lapangan/reference; Intel adalah jaringan sensor lab. Tidak satu pun memvalidasi sensor RAB proyek.",
            "- Jumlah parameter dan latency CPU tidak sama dengan konsumsi energi.",
            "- Mean MASE tidak boleh dipakai sendiri untuk memilih model; skill, seed stability, seri/lokasi, dan resource harus dibaca bersama.",
            "- Hasil negatif tidak dihapus dan menjadi bagian inti argumentasi.",
        ]
    )
    report_path = args.output_dir / "ANALYSIS_REPORT_V3D.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    hashes = {
        path.name: sha256_file(path)
        for path in (manifest_path, decision_path, target_path, report_path, args.summary)
    }
    (args.output_dir / "ANALYSIS_HASHES_V3D.json").write_text(
        json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps({"runs": len(runs), "matrix": matrix, "outputs": hashes}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
