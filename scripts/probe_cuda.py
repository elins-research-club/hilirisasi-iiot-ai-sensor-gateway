#!/usr/bin/env python3
"""Print torch/CUDA probe for bake-off scripts (Windows-safe)."""
from __future__ import annotations


def main() -> int:
    import torch

    print("torch", torch.__version__)
    print("cuda", torch.cuda.is_available())
    print("device", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
