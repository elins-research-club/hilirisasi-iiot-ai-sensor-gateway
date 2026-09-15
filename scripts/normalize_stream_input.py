#!/usr/bin/env python3
"""Build normalized stream-detect input (features in [0,1]) from raw stream JSONL.

Matches the `_norm.jsonl` files used in earlier VPS runs: min-max normalization
computed over the whole series per feature, values clamped to [0,1]. Preserves
uptime provenance and carries error-marker rows as empty feature objects so the
detector rejects them instead of learning from a fabricated value.

Usage: python3 scripts/normalize_stream_input.py <input.jsonl> <output.jsonl>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    args = ap.parse_args()
    inp = Path(args.input)
    out = Path(args.output)
    rows = []
    with inp.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    if not rows:
        print("empty input", file=sys.stderr)
        return 1
    # Gather only valid feature values.  Error sentinels are marked by the
    # stream builder and must not become a seemingly valid normalized minimum.
    all_feats = sorted({k for r in rows for k in r.get("features", {})})
    series = {k: [] for k in all_feats}
    for r in rows:
        invalid = set(r.get("invalid_fields", ()))
        for k in all_feats:
            series[k].append(None if k in invalid else r.get("features", {}).get(k))
    arr = {k: np.array(v, dtype=float) for k, v in series.items()}
    finite = {k: v[np.isfinite(v)] for k, v in arr.items()}
    unusable = [k for k, values in finite.items() if values.size == 0]
    if unusable:
        print(f"no finite values for feature(s): {unusable}", file=sys.stderr)
        return 1
    lo = {k: float(values.min()) for k, values in finite.items()}
    hi = {k: float(values.max()) for k, values in finite.items()}
    span = {k: (hi[k] - lo[k]) or 1.0 for k in all_feats}
    with out.open("w", encoding="utf-8") as w:
        for r in rows:
            feats = {}
            for k in all_feats:
                v = r.get("features", {}).get(k)
                if v is None:
                    continue
                norm = (float(v) - lo[k]) / span[k]
                norm = max(0.0, min(1.0, norm))
                feats[k] = norm
            rec = {"features": feats}
            for key in ("timestamp", "node_timestamp_ms", "timestamp_basis", "invalid_fields"):
                if key in r:
                    rec[key] = r[key]
            w.write(json.dumps(rec, separators=(",", ":")) + "\n")
    print(f"{inp.name}: {len(rows)} rows -> {out.name} (features={all_feats})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
