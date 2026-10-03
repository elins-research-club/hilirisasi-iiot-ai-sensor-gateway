#!/usr/bin/env python3
"""Cross-platform, resume-safe multi-lane CUDA/CPU bake-off orchestrator.

The runner never guesses downloads by size, stores one output directory per
seed, writes state atomically, and resumes only when command + source
fingerprints match. It does not turn laptop results into Raspberry Pi evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STATE_SCHEMA = "iiot.ai_sensor.bakeoff_runner_state.v1"
DEFAULT_MAX_NORMALIZATION_CLIP_FRACTION = 0.05


@dataclass(frozen=True)
class Lane:
    slug: str
    payloads: Path
    targets: tuple[str, ...]
    window_size: int
    seasonal_period: int
    max_samples_per_split: int = 0
    max_window_records: int = 0
    artifact_root: Path = ROOT / "models" / "bakeoff"

    @property
    def processed_dir(self) -> Path:
        return self.artifact_root / "_run_data" / self.slug / "processed"

    @property
    def dataset_path(self) -> Path:
        return self.artifact_root / "_run_data" / self.slug / "forecast.npz"

    @property
    def meta_path(self) -> Path:
        return self.artifact_root / "_run_data" / self.slug / "forecast-meta.json"

    @property
    def models_root(self) -> Path:
        return self.artifact_root / self.slug


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


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema": STATE_SCHEMA,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "steps": {},
            "blocked_lanes": {},
        }
    state = json.loads(path.read_text(encoding="utf-8-sig"))
    if state.get("schema") != STATE_SCHEMA:
        raise ValueError(f"unsupported runner state schema: {state.get('schema')}")
    return state


def source_fingerprint() -> str:
    digest = hashlib.sha256()
    roots = (
        ROOT / "src",
        ROOT / "scripts",
        ROOT / "config",
        ROOT / "schemas",
    )
    files: list[Path] = []
    for base in roots:
        if not base.exists():
            continue
        files.extend(
            path
            for path in base.rglob("*")
            if path.is_file()
            and path.suffix.lower() in {".py", ".toml", ".json", ".sh", ".ps1", ".cmd"}
            and "__pycache__" not in path.parts
            # Job transport does not affect preprocessing/training/evaluation.
            # Excluding it prevents an agent-only hotfix from invalidating a
            # completed multi-hour bake-off fingerprint.
            and not (base == ROOT / "scripts" and "remote" in path.relative_to(base).parts)
        )
    for path in sorted(files):
        digest.update(str(path.relative_to(ROOT)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def input_fingerprint(paths: list[Path] | tuple[Path, ...]) -> str:
    """Hash declared step inputs so resume cannot reuse stale outputs.

    Missing inputs are included in the digest too: a file appearing at the same
    path after a failed/partial run must invalidate the previous step state.
    """
    digest = hashlib.sha256()
    for path in sorted((Path(item) for item in paths), key=lambda item: str(item)):
        digest.update(str(path).encode("utf-8"))
        digest.update(b"\0")
        if not path.is_file():
            digest.update(b"<missing-or-not-a-file>\0")
            continue
        with path.open("rb") as file:
            while chunk := file.read(1024 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def command_fingerprint(
    command: list[str],
    code_fingerprint: str,
    input_digest: str = "",
) -> str:
    payload = json.dumps(
        {
            "command": command,
            "source_fingerprint": code_fingerprint,
            "input_fingerprint": input_digest,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def outputs_ready(outputs: list[Path]) -> bool:
    # Empty output lists mean "side-effect / probe only". Resume is allowed when the
    # command fingerprint still matches; do not force re-run just because no files
    # were declared.
    if not outputs:
        return True
    return all(path.is_file() and path.stat().st_size > 0 for path in outputs)


def normalization_clipping_gate(
    report: dict[str, Any],
    *,
    max_clip_fraction: float,
) -> dict[str, Any]:
    """Evaluate preprocessing clipping before any model is trained.

    The gate is deliberately feature-wise as well as global: a low aggregate
    clip rate must not hide one sensor channel that is almost always clipped.
    """

    if not 0.0 <= max_clip_fraction <= 1.0:
        raise ValueError("max_clip_fraction must be in [0, 1]")
    if report.get("schema") != "iiot.ai_sensor.normalization_report.v1":
        return {
            "status": "FAIL",
            "reason": "normalization_report_missing_or_unsupported",
            "max_clip_fraction": max_clip_fraction,
        }
    overall = float(report.get("clip_fraction", 0.0))
    fields = report.get("fields", {})
    field_fractions = {
        str(name): float(item.get("clip_fraction", 0.0))
        for name, item in fields.items()
        if isinstance(item, dict) and int(item.get("seen", 0)) > 0
    }
    worst_field = max(field_fractions, key=field_fractions.get) if field_fractions else None
    worst_fraction = field_fractions.get(worst_field, 0.0) if worst_field else 0.0
    passed = overall <= max_clip_fraction and worst_fraction <= max_clip_fraction
    return {
        "status": "PASS" if passed else "FAIL",
        "reason": None if passed else "normalization_clipping_exceeds_gate",
        "max_clip_fraction": max_clip_fraction,
        "overall_clip_fraction": overall,
        "worst_field": worst_field,
        "worst_field_clip_fraction": worst_fraction,
    }


class Runner:
    def __init__(
        self,
        *,
        python: str,
        state_path: Path,
        artifact_root: Path,
        force: bool,
        dry_run: bool,
    ) -> None:
        self.python = python
        self.state_path = state_path
        self.artifact_root = artifact_root
        self.force = force
        self.dry_run = dry_run
        self.state = load_state(state_path)
        self.code_fingerprint = source_fingerprint()
        self.env = os.environ.copy()
        self.env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + self.env.get("PYTHONPATH", "")

    def save(self) -> None:
        self.state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.state["source_fingerprint"] = self.code_fingerprint
        atomic_write_json(self.state_path, self.state)

    def run_step(
        self,
        key: str,
        args: list[str],
        outputs: list[Path],
        *,
        input_paths: list[Path] | tuple[Path, ...] = (),
    ) -> None:
        command = [self.python, *args]
        fingerprint = command_fingerprint(
            command,
            self.code_fingerprint,
            input_fingerprint(input_paths),
        )
        previous = self.state["steps"].get(key, {})
        if (
            not self.force
            and previous.get("status") == "completed"
            and previous.get("fingerprint") == fingerprint
            and outputs_ready(outputs)
        ):
            print(f"SKIP {key}: matching completed state")
            return
        print("RUN", key)
        print("   ", " ".join(command))
        if self.dry_run:
            self.state["steps"][key] = {
                "status": "dry_run",
                "fingerprint": fingerprint,
                "command": command,
                "outputs": [str(path) for path in outputs],
            }
            self.save()
            return
        started = time.time()
        self.state["steps"][key] = {
            "status": "running",
            "fingerprint": fingerprint,
            "command": command,
            "outputs": [str(path) for path in outputs],
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self.save()
        try:
            subprocess.run(command, cwd=ROOT, env=self.env, check=True)
            if outputs and not outputs_ready(outputs):
                missing = [str(path) for path in outputs if not path.is_file() or path.stat().st_size == 0]
                raise RuntimeError(f"step completed without required outputs: {missing}")
        except Exception as exc:
            self.state["steps"][key].update(
                {
                    "status": "failed",
                    "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            self.save()
            raise
        self.state["steps"][key].update(
            {
                "status": "completed",
                "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "elapsed_seconds": round(time.time() - started, 3),
                "error": None,
            }
        )
        self.save()

    def internal_step(self, key: str, fingerprint_payload: dict[str, Any], outputs: list[Path], action) -> None:
        fingerprint = hashlib.sha256(
            json.dumps(
                {**fingerprint_payload, "source_fingerprint": self.code_fingerprint},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        previous = self.state["steps"].get(key, {})
        if (
            not self.force
            and previous.get("status") == "completed"
            and previous.get("fingerprint") == fingerprint
            and outputs_ready(outputs)
        ):
            print(f"SKIP {key}: matching completed state")
            return
        print("RUN", key)
        if self.dry_run:
            self.state["steps"][key] = {
                "status": "dry_run",
                "fingerprint": fingerprint,
                "outputs": [str(path) for path in outputs],
            }
            self.save()
            return
        self.state["steps"][key] = {"status": "running", "fingerprint": fingerprint}
        self.save()
        try:
            action()
            if not outputs_ready(outputs):
                raise RuntimeError("internal step did not produce required outputs")
        except Exception as exc:
            self.state["steps"][key].update(
                {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            )
            self.save()
            raise
        self.state["steps"][key].update({"status": "completed", "error": None})
        self.save()


def parse_csv_ints(value: str) -> tuple[int, ...]:
    values = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    if not values or any(item < 0 for item in values):
        raise argparse.ArgumentTypeError("expected comma-separated non-negative integers")
    return values


def parse_csv_strings(value: str) -> tuple[str, ...]:
    values = tuple(item.strip() for item in value.split(",") if item.strip())
    if not values:
        raise argparse.ArgumentTypeError("expected comma-separated values")
    return values


def prepare_public_data(runner: Runner, args: argparse.Namespace) -> None:
    downloads = runner.artifact_root / "_run_data" / "external" / "downloads"
    adapted = runner.artifact_root / "_run_data" / "external" / "adapted"
    downloads.mkdir(parents=True, exist_ok=True)
    adapted.mkdir(parents=True, exist_ok=True)
    uci_zip = downloads / "uci_air_quality_360.zip"
    fidas_csv = downloads / "zenodo_fidas_pm_reference_7198378.csv"
    if not args.skip_download:
        runner.run_step(
            "download/uci",
            ["scripts/download_dataset.py", "uci_air_quality_360", "--output-dir", str(downloads), "--max-bytes", str(args.max_bytes)],
            [uci_zip],
        )
        runner.run_step(
            "download/fidas",
            ["scripts/download_dataset.py", "zenodo_fidas_pm_reference_7198378", "--output-dir", str(downloads), "--max-bytes", str(args.max_bytes)],
            [fidas_csv],
        )
    uci_csv = downloads / "AirQualityUCI.csv"
    if uci_zip.exists():
        def extract_uci() -> None:
            with zipfile.ZipFile(uci_zip) as archive:
                matches = [name for name in archive.namelist() if Path(name).name == "AirQualityUCI.csv"]
                if len(matches) != 1:
                    raise ValueError(f"expected exactly one AirQualityUCI.csv, found {matches}")
                data = archive.read(matches[0])
            uci_csv.write_bytes(data)
        runner.internal_step(
            "extract/uci",
            {"archive": str(uci_zip), "sha256": hashlib.sha256(uci_zip.read_bytes()).hexdigest()},
            [uci_csv],
            extract_uci,
        )
    if uci_csv.exists():
        uci_jsonl = adapted / "uci.jsonl"
        uci_compact = adapted / "uci_compact.jsonl"
        runner.run_step(
            "adapt/uci",
            ["run_gateway.py", "adapt-uci-air-quality", "--input-csv", str(uci_csv), "--output", str(uci_jsonl)],
            [uci_jsonl],
            input_paths=[uci_csv],
        )
        runner.run_step(
            "compact/uci",
            ["scripts/dataset_record_to_compact.py", "--input", str(uci_jsonl), "--output", str(uci_compact), "--gateway-id", "uci_gateway"],
            [uci_compact],
            input_paths=[uci_jsonl],
        )
    if fidas_csv.exists():
        fidas_jsonl = adapted / "fidas.jsonl"
        fidas_compact = adapted / "fidas_compact.jsonl"
        runner.run_step(
            "adapt/fidas",
            ["run_gateway.py", "adapt-zenodo-pm-reference", "--input-csv", str(fidas_csv), "--output", str(fidas_jsonl)],
            [fidas_jsonl],
            input_paths=[fidas_csv],
        )
        runner.run_step(
            "compact/fidas",
            ["scripts/dataset_record_to_compact.py", "--input", str(fidas_jsonl), "--output", str(fidas_compact), "--gateway-id", "fidas_gateway", "--include-reference-as-sensor"],
            [fidas_compact],
            input_paths=[fidas_jsonl],
        )


def lane_definitions(artifact_root: Path | None = None) -> dict[str, Lane]:
    artifact_root = artifact_root or ROOT / "models" / "bakeoff"
    return {
        "gary": Lane(
            "gary",
            ROOT / "data" / "derived" / "gary_project_sensor_payloads.jsonl",
            ("temperature_c", "humidity_pct", "pressure_hpa"),
            48,
            0,
            artifact_root=artifact_root,
        ),
        "uci": Lane(
            "uci",
            artifact_root / "_run_data" / "external" / "adapted" / "uci_compact.jsonl",
            ("temperature_c", "humidity_pct"),
            48,
            24,
            artifact_root=artifact_root,
        ),
        "fidas": Lane(
            "fidas",
            artifact_root / "_run_data" / "external" / "adapted" / "fidas_compact.jsonl",
            ("pm25_ug_m3", "pm1_ug_m3", "pm10_ug_m3"),
            12,
            720,
            20000,
            50000,
            artifact_root=artifact_root,
        ),
        "sim": Lane(
            "sim",
            artifact_root / "_run_data" / "sim" / "payloads.jsonl",
            (
                "temperature_c",
                "humidity_pct",
                "pressure_hpa",
                "co_ppm",
                "o3_ppm",
                "co2_ppm",
                "pm25_ug_m3",
            ),
            12,
            1440,
            artifact_root=artifact_root,
        ),
    }


def resolve_artifact_root(value: str) -> Path:
    """Resolve a run-specific artifact directory inside this repository."""
    relative = Path(value) if value else Path("models") / "bakeoff"
    if relative.is_absolute():
        raise SystemExit("--run-dir must be relative to the repository root")
    if ".." in relative.parts:
        raise SystemExit("--run-dir cannot contain parent traversal")
    root = ROOT.resolve()
    runs_root = root / "models" / "bakeoff_runs"
    _assert_no_reparse_components(runs_root, root)
    candidate = root / relative
    _assert_no_reparse_components(candidate, root)
    artifact_root = candidate.resolve()
    try:
        artifact_root.relative_to(runs_root)
    except ValueError as exc:
        raise SystemExit("--run-dir must stay inside models/bakeoff_runs") from exc
    if artifact_root == runs_root:
        raise SystemExit("--run-dir must name a child directory under models/bakeoff_runs")
    return artifact_root


def _is_reparse_point(path: Path) -> bool:
    """Return true for a symlink or Windows junction."""
    is_junction = getattr(os.path, "isjunction", lambda _path: False)
    return path.is_symlink() or bool(is_junction(str(path)))


def _assert_no_reparse_components(path: Path, root: Path) -> None:
    """Reject symlink/junction components before resolving a user path."""
    root = root.resolve()
    candidate = path.absolute()
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise SystemExit("path must stay inside the repository root") from exc
    current = root
    for part in relative.parts:
        current /= part
        if _is_reparse_point(current):
            raise SystemExit(f"path component cannot be a symlink/junction: {current}")


def _assert_no_reparse_tree(root: Path) -> None:
    """Reject existing symlinks/junctions anywhere in a resumable run."""
    if not root.exists():
        return
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in [*directories, *files]:
            candidate = current_path / name
            if _is_reparse_point(candidate):
                raise SystemExit(
                    f"run directory contains a symlink/junction; refusing resume: {candidate}"
                )


def create_new_run_dir() -> Path:
    """Atomically create a fresh timestamped run directory inside the repo."""
    parent = ROOT / "models" / "bakeoff_runs"
    _assert_no_reparse_components(parent, ROOT)
    parent.mkdir(parents=True, exist_ok=True)
    _assert_no_reparse_components(parent, ROOT)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    for suffix in ("", "_01", "_02", "_03", "_04", "_05"):
        candidate = parent / f"{stamp}{suffix}"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise SystemExit(f"could not allocate a unique run directory under {parent}")


def ensure_artifact_root_safe(artifact_root: Path, *, resume: bool) -> None:
    """Prevent accidental reuse of the legacy or a non-empty run directory."""
    # ``resolve_artifact_root`` already enforces this for the real CLI path.
    # Keep this helper usable with isolated temporary roots in unit tests.
    try:
        artifact_root.absolute().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        _assert_no_reparse_components(artifact_root, ROOT)
    legacy_root = (ROOT / "models" / "bakeoff").resolve()
    if artifact_root.resolve() == legacy_root:
        raise SystemExit(
            "models/bakeoff is the protected legacy run; use models/bakeoff_runs/<name>"
        )
    if artifact_root.exists() and any(artifact_root.iterdir()) and not resume:
        raise SystemExit(
            f"run directory is non-empty: {artifact_root}; "
            "choose a new --run-dir or pass --resume explicitly"
        )
    _assert_no_reparse_tree(artifact_root)


def resolve_state_path(value: str, artifact_root: Path) -> Path:
    """Keep custom state files inside the selected run directory."""
    _assert_no_reparse_tree(artifact_root)
    state = (ROOT / value).resolve() if value else artifact_root / "state" / "RUN_STATE.json"
    _assert_no_reparse_components(state, artifact_root)
    try:
        state.relative_to(artifact_root.resolve())
    except ValueError as exc:
        raise SystemExit("--state must stay inside the selected --run-dir") from exc
    return state


def prepare_lane(runner: Runner, lane: Lane, args: argparse.Namespace) -> bool:
    if lane.slug == "sim":
        runner.run_step(
            "data/simulate",
            [
                "run_gateway.py", "simulate", "--scenario", "mixed", "--count", str(args.sim_count),
                "--nodes", str(args.sim_nodes), "--interval-sec", "60", "--output", str(lane.payloads),
            ],
            [lane.payloads],
        )
    if not lane.payloads.exists() and not runner.dry_run:
        print(f"BLOCK lane {lane.slug}: payload file missing: {lane.payloads}")
        runner.state["blocked_lanes"][lane.slug] = {
            "reason": "payload_file_missing",
            "path": str(lane.payloads),
        }
        runner.save()
        return False
    runner.run_step(
        f"lane/{lane.slug}/preprocess",
        ["run_gateway.py", "run", "--input-file", str(lane.payloads), "--output-dir", str(lane.processed_dir)],
        [
            lane.processed_dir / "raw_payloads.jsonl",
            lane.processed_dir / "hardware_observations.jsonl",
            lane.processed_dir / "canonical_observations.jsonl",
            lane.processed_dir / "processed_timeseries.jsonl",
            lane.processed_dir / "windows.jsonl",
            lane.processed_dir / "normalization_report.json",
        ],
        input_paths=[lane.payloads],
    )
    runner.run_step(
        f"lane/{lane.slug}/dataset",
        [
            "scripts/prepare_lane_forecast.py",
            "--windows", str(lane.processed_dir / "windows.jsonl"),
            "--output-npz", str(lane.dataset_path),
            "--output-meta", str(lane.meta_path),
            "--targets", ",".join(lane.targets),
            "--horizon-steps", str(args.horizon),
            "--window-size", str(lane.window_size),
            "--purge-gap-steps", str(args.purge_gap),
            "--max-samples-per-split", str(lane.max_samples_per_split),
            "--max-window-records", str(lane.max_window_records),
        ],
        [lane.dataset_path, lane.meta_path],
        input_paths=[lane.processed_dir / "windows.jsonl"],
    )
    if runner.dry_run:
        return True
    normalization_path = lane.processed_dir / "normalization_report.json"
    if not normalization_path.is_file():
        clip_gate = normalization_clipping_gate(
            {}, max_clip_fraction=args.max_normalization_clip_fraction
        )
    else:
        clip_gate = normalization_clipping_gate(
            json.loads(normalization_path.read_text(encoding="utf-8-sig")),
            max_clip_fraction=args.max_normalization_clip_fraction,
        )
    runner.state.setdefault("normalization_gates", {})[lane.slug] = clip_gate
    if clip_gate["status"] != "PASS":
        print(
            f"BLOCK lane {lane.slug}: normalization clipping gate FAIL "
            f"(overall={clip_gate.get('overall_clip_fraction')}, "
            f"worst={clip_gate.get('worst_field')}:{clip_gate.get('worst_field_clip_fraction')})"
        )
        runner.state["blocked_lanes"][lane.slug] = {
            "reason": "normalization_clipping_gate_fail",
            "normalization_gate": clip_gate,
        }
        runner.save()
        return False
    meta = json.loads(lane.meta_path.read_text(encoding="utf-8"))
    quality = meta.get("data_quality", {})
    if quality.get("status") == "FAIL":
        print(f"BLOCK lane {lane.slug}: dataset quality FAIL")
        runner.state["blocked_lanes"][lane.slug] = {
            "reason": "dataset_quality_fail",
            "data_quality": quality,
        }
        runner.save()
        return False
    runner.state["blocked_lanes"].pop(lane.slug, None)
    runner.save()
    return True


def train_lane(runner: Runner, lane: Lane, args: argparse.Namespace) -> None:
    for seed in args.seeds:
        for model_type in ("fits", "fits_official", "dlinear"):
            output = lane.models_root / model_type / f"seed_{seed}"
            model = output / "model.pt"
            runner.run_step(
                f"lane/{lane.slug}/{model_type}/seed_{seed}/train",
                [
                    "run_gateway.py", "train-edge-forecast",
                    "--dataset", str(lane.dataset_path),
                    "--output-dir", str(output),
                    "--model-type", model_type,
                    "--epochs", str(args.epochs),
                    "--batch-size", str(args.batch_size),
                    "--patience", str(args.patience),
                    "--seed", str(seed),
                    "--device", args.device,
                    "--learning-rate", str(args.learning_rate),
                    *(
                        ["--individual"]
                        if model_type == "fits_official" and args.fits_official_individual
                        else []
                    ),
                ],
                [model, output / "training.json"],
                input_paths=[lane.dataset_path],
            )
            runner.run_step(
                f"lane/{lane.slug}/{model_type}/seed_{seed}/evaluate",
                [
                    "run_gateway.py", "evaluate-edge-forecast",
                    "--dataset", str(lane.dataset_path),
                    "--model", str(model),
                    "--output-dir", str(output),
                    "--device", args.device,
                    "--batch-size", str(args.eval_batch_size),
                    "--seasonal-period", str(lane.seasonal_period),
                ],
                [output / "metrics.json"],
                input_paths=[lane.dataset_path, model],
            )
        if not args.skip_lstm:
            output = lane.models_root / "lstm_residual" / f"seed_{seed}"
            model = output / "model.pt"
            runner.run_step(
                f"lane/{lane.slug}/lstm_residual/seed_{seed}/train",
                [
                    "run_gateway.py", "train-lstm-forecast",
                    "--dataset", str(lane.dataset_path),
                    "--output-dir", str(output),
                    "--epochs", str(args.lstm_epochs),
                    "--batch-size", str(args.batch_size),
                    "--patience", str(args.patience),
                    "--seed", str(seed),
                    "--device", args.device,
                    "--forecast-strategy", "residual",
                ],
                [model, output / "training.json"],
                input_paths=[lane.dataset_path],
            )
            runner.run_step(
                f"lane/{lane.slug}/lstm_residual/seed_{seed}/evaluate",
                [
                    "run_gateway.py", "evaluate-lstm-forecast",
                    "--dataset", str(lane.dataset_path),
                    "--model", str(model),
                    "--output-dir", str(output),
                    "--device", args.device,
                    "--eval-batch-size", str(args.eval_batch_size),
                    "--seasonal-period", str(lane.seasonal_period),
                ],
                [output / "metrics.json"],
                input_paths=[lane.dataset_path, model],
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--lstm-epochs", type=int, default=40)
    parser.add_argument("--patience", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--eval-batch-size", type=int, default=1024)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--purge-gap", type=int, default=5)
    parser.add_argument(
        "--max-normalization-clip-fraction",
        type=float,
        default=DEFAULT_MAX_NORMALIZATION_CLIP_FRACTION,
        help=(
            "block a lane before training when overall or any feature clipping "
            "exceeds this fraction (default: 0.05)"
        ),
    )
    parser.add_argument("--seeds", type=parse_csv_ints, default=(42, 43, 44))
    parser.add_argument("--lanes", type=parse_csv_strings, default=("gary", "uci", "fidas", "sim"))
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--skip-public-prepare", action="store_true")
    parser.add_argument("--skip-lstm", action="store_true")
    parser.add_argument(
        "--fits-official-individual",
        action="store_true",
        help="train fits_official with per-channel frequency upsampler (paper-style individual)",
    )
    parser.add_argument("--max-bytes", type=int, default=100 * 1024 * 1024)
    parser.add_argument("--sim-count", type=int, default=5000)
    parser.add_argument("--sim-nodes", type=int, default=3)
    parser.add_argument(
        "--run-dir",
        default="",
        help="optional repository-relative artifact directory; omitted = fresh timestamped run",
    )
    parser.add_argument(
        "--state",
        default="",
        help="optional state path; defaults to <run-dir>/state/RUN_STATE.json",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="allow resuming a non-empty --run-dir explicitly",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def validate_args(args: argparse.Namespace) -> None:
    positive = {
        "epochs": args.epochs,
        "lstm_epochs": args.lstm_epochs,
        "patience": args.patience,
        "batch_size": args.batch_size,
        "eval_batch_size": args.eval_batch_size,
        "horizon": args.horizon,
        "sim_count": args.sim_count,
        "sim_nodes": args.sim_nodes,
        "max_bytes": args.max_bytes,
    }
    invalid = [name for name, value in positive.items() if value < 1]
    if invalid:
        raise SystemExit(f"positive arguments required: {invalid}")
    if args.purge_gap < 0 or args.learning_rate <= 0:
        raise SystemExit("purge-gap must be non-negative and learning-rate positive")
    if not 0.0 <= args.max_normalization_clip_fraction <= 1.0:
        raise SystemExit("max-normalization-clip-fraction must be in [0, 1]")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    validate_args(args)
    if args.resume and not args.run_dir:
        raise SystemExit("--resume requires an explicit --run-dir")
    os.chdir(ROOT)
    artifact_root = create_new_run_dir() if not args.run_dir else resolve_artifact_root(args.run_dir)
    ensure_artifact_root_safe(artifact_root, resume=args.resume)
    lane_map = lane_definitions(artifact_root)
    unknown = sorted(set(args.lanes).difference(lane_map))
    if unknown:
        raise SystemExit(f"unknown lanes: {unknown}")
    runner = Runner(
        python=args.python,
        state_path=resolve_state_path(args.state, artifact_root),
        artifact_root=artifact_root,
        force=args.force,
        dry_run=args.dry_run,
    )
    runner.run_step("device/probe", ["scripts/probe_cuda.py"], [])
    if not args.skip_public_prepare and any(lane in {"uci", "fidas"} for lane in args.lanes):
        prepare_public_data(runner, args)
    for lane_name in args.lanes:
        lane = lane_map[lane_name]
        if prepare_lane(runner, lane, args):
            train_lane(runner, lane, args)
    runner.run_step(
        "summary",
        [
            "scripts/summarize_bakeoff.py",
            "--root", str(artifact_root),
            "--output", str(artifact_root / "FULL_BAKEOFF_SUMMARY.json"),
        ],
        [artifact_root / "FULL_BAKEOFF_SUMMARY.json"],
        input_paths=sorted(artifact_root.rglob("metrics.json")),
    )
    print("Bake-off orchestration complete. Results remain EXPERIMENTAL.")
    print("State:", runner.state_path)
    print("Artifacts:", artifact_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
