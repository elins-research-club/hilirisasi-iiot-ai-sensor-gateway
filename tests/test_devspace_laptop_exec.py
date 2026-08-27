from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "remote" / "devspace_laptop_exec.py"
SPEC = importlib.util.spec_from_file_location("devspace_laptop_exec", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_accepts_canonical_cuda_probe_script() -> None:
    command = ["py", "-3.13", "scripts\\probe_cuda.py"]
    assert MODULE.validate_argv(command) == command


def test_accepts_canonical_bakeoff_dry_run() -> None:
    command = [
        "py",
        "-3.13",
        "scripts\\laptop_bakeoff_runner.py",
        "--device",
        "cuda",
        "--dry-run",
        "--lanes",
        "sim",
    ]
    assert MODULE.validate_argv(command) == command


@pytest.mark.parametrize(
    "command",
    [
        ["powershell", "-Command", "Get-ChildItem"],
        ["py", "-3.13", "-c", "print('unscoped')"],
        ["py", "-3.13", "..\\outside.py"],
        ["py", "-3.13", "C:\\Windows\\Temp\\outside.py"],
        ["git", "checkout", "other-branch"],
        ["tools\\python.exe", "scripts\\probe_cuda.py"],
        ["nvidia-smi", "--gpu-reset"],
        ["nvidia-smi", "--power-limit=100"],
    ],
)
def test_blocks_unscoped_commands(command: list[str]) -> None:
    with pytest.raises(MODULE.ValidationError):
        MODULE.validate_argv(command)


def test_cwd_must_remain_relative() -> None:
    with pytest.raises(MODULE.ValidationError):
        MODULE.validate_relative_windows_path("..\\outside", label="Working directory")


def test_accepts_read_only_nvidia_query() -> None:
    command = ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"]
    assert MODULE.validate_argv(command) == command


def test_encoded_powershell_roundtrip_shape() -> None:
    encoded = MODULE.encoded_powershell("Write-Output 'ok'")
    assert encoded
    assert "Write-Output" not in encoded


def test_remote_script_prepends_repo_src_to_pythonpath() -> None:
    script = MODULE.remote_run_script(
        {"repo": "iiot-ai-sensor-gateway", "cwd": ".", "argv": ["py", "scripts\\probe_cuda.py"]}
    )
    assert "$env:PYTHONPATH = $sourceRoot" in script
