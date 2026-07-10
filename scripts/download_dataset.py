#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def load_catalog(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != "iiot.dataset_catalog.v1":
        raise ValueError("unsupported dataset catalog schema")
    datasets = data.get("datasets")
    if not isinstance(datasets, list):
        raise ValueError("dataset catalog must contain a datasets list")
    return data


def find_entry(catalog: dict[str, Any], dataset_id: str) -> dict[str, Any]:
    for entry in catalog["datasets"]:
        if entry.get("id") == dataset_id:
            return entry
    raise ValueError(f"unknown dataset id: {dataset_id}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(
    entry: dict[str, Any],
    output_dir: Path,
    *,
    max_bytes: int,
    expected_sha256: str | None,
) -> Path:
    if entry.get("download_mode") != "script_allowed":
        raise ValueError(
            f"dataset {entry['id']} is {entry.get('download_mode')}; follow its landing page and licence manually"
        )
    url = entry.get("download_url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ValueError("catalog download URL must be an HTTPS URL")
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = Path(urllib.parse.urlparse(url).path).name or f"{entry['id']}.download"
    destination = output_dir / filename
    if destination.exists():
        actual = sha256_file(destination)
        if expected_sha256 and actual.lower() != expected_sha256.lower():
            raise ValueError(f"existing file checksum mismatch: {destination}")
        return destination

    request = urllib.request.Request(url, headers={"User-Agent": "iiot-ai-sensor-gateway/1.0"})
    temporary: Path | None = None
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                raise ValueError(
                    f"download size {content_length} exceeds safety limit {max_bytes} bytes"
                )
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=f".{filename}.", dir=output_dir, delete=False
            ) as file:
                temporary = Path(file.name)
                copied = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > max_bytes:
                        raise ValueError(f"download exceeded safety limit {max_bytes} bytes")
                    file.write(chunk)
                file.flush()
                os.fsync(file.fileno())
        actual = sha256_file(temporary)
        if expected_sha256 and actual.lower() != expected_sha256.lower():
            raise ValueError(f"download checksum mismatch: got {actual}")
        os.replace(temporary, destination)
        temporary = None
        metadata = {
            "dataset_id": entry["id"],
            "source_url": url,
            "landing_url": entry.get("landing_url"),
            "license": entry.get("license"),
            "sha256": actual,
            "bytes": destination.stat().st_size,
        }
        destination.with_suffix(destination.suffix + ".source.json").write_text(
            json.dumps(metadata, indent=2), encoding="utf-8"
        )
        return destination
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download an allow-listed public dataset safely")
    parser.add_argument("dataset_id", nargs="?")
    parser.add_argument("--catalog", default="datasets/catalog.json")
    parser.add_argument("--output-dir", default="data/external/downloads")
    parser.add_argument("--max-bytes", type=int, default=50 * 1024 * 1024)
    parser.add_argument("--sha256", default=None)
    parser.add_argument("--describe-only", action="store_true")
    parser.add_argument("--list", action="store_true", dest="list_datasets")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_bytes < 1:
        raise SystemExit("--max-bytes must be positive")
    catalog = load_catalog(Path(args.catalog))
    if args.list_datasets:
        for entry in catalog["datasets"]:
            print(
                "\t".join(
                    (
                        str(entry.get("id", "")),
                        str(entry.get("download_mode", "")),
                        str(entry.get("license", "")),
                    )
                )
            )
        return 0
    if not args.dataset_id:
        raise SystemExit("dataset_id is required unless --list is used")
    entry = find_entry(catalog, args.dataset_id)
    if args.describe_only:
        print(json.dumps(entry, indent=2))
        return 0
    try:
        catalog_sha256 = entry.get("sha256")
        expected_sha256 = args.sha256 or (
            str(catalog_sha256) if isinstance(catalog_sha256, str) else None
        )
        path = download(
            entry,
            Path(args.output_dir),
            max_bytes=args.max_bytes,
            expected_sha256=expected_sha256,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
