#!/usr/bin/env python3
"""Run scoped IIOT checks and experiments on the Windows CUDA laptop via SSH.

This is the canonical DevSpace/GPT entrypoint. It intentionally avoids a remote
shell: the caller selects a registered repository and passes an argv array for a
small allowlist of development executables. The Windows working directory is
always inside C:\\vscode\\IIOT-Project.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
from pathlib import PureWindowsPath

SSH_HOST = "grey-laptop"
REPO_ROOTS = {
    "iiot-ai-sensor-gateway": r"C:\vscode\IIOT-Project\iiot-ai-sensor-gateway",
    "yolo_vision_gateway": r"C:\vscode\IIOT-Project\yolo_vision_gateway",
    "iiot_person_counter": r"C:\vscode\IIOT-Project\archive\prototypes\iiot_person_counter",
}
ALLOWED_EXECUTABLES = {
    "nvidia-smi",
    "nvidia-smi.exe",
    "py",
    "py.exe",
    "pytest",
    "pytest.exe",
    "python",
    "python.exe",
}
ALLOWED_PYTHON_MODULES = {"pytest", "unittest"}


class ValidationError(ValueError):
    """Raised when a requested remote command is outside the scoped contract."""


def validate_relative_windows_path(value: str, *, label: str) -> str:
    if not value or value == ".":
        return "."
    path = PureWindowsPath(value)
    if path.is_absolute() or path.drive or path.root or ".." in path.parts:
        raise ValidationError(f"{label} must stay inside the selected repository: {value!r}")
    return str(path)


def validate_argument(value: str) -> None:
    if not value or any(character in value for character in ("\x00", "\n", "\r")):
        raise ValidationError("empty arguments and control characters are not allowed")
    if re.search(r"(?:^|=)[A-Za-z]:[\\/]", value) or value.startswith(("\\\\", "//")):
        raise ValidationError(f"absolute Windows paths are not allowed: {value!r}")
    if ".." in re.split(r"[\\/]", value):
        raise ValidationError(f"parent traversal is not allowed: {value!r}")


def validate_python_arguments(arguments: list[str]) -> None:
    if not arguments:
        raise ValidationError("Python requires a repository-relative script or an allowed module")
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "-c":
            raise ValidationError("python -c is blocked; run a reviewed repository script instead")
        if argument == "-m":
            if index + 1 >= len(arguments) or arguments[index + 1] not in ALLOWED_PYTHON_MODULES:
                raise ValidationError("only python -m pytest or python -m unittest is allowed")
            return
        if argument in {"-X", "-W"}:
            if index + 1 >= len(arguments):
                raise ValidationError(f"{argument} requires a value")
            index += 2
            continue
        if argument.startswith("-3.") or argument in {
            "-B",
            "-E",
            "-I",
            "-O",
            "-OO",
            "-S",
            "-s",
            "-u",
        }:
            index += 1
            continue
        if argument.startswith("-"):
            raise ValidationError(f"unsupported Python interpreter option: {argument!r}")
        script = validate_relative_windows_path(argument, label="Python script")
        if PureWindowsPath(script).suffix.lower() != ".py":
            raise ValidationError("Python entrypoint must be a repository-relative .py file")
        return
    raise ValidationError("Python requires a repository-relative script")


def validate_argv(argv: list[str]) -> list[str]:
    if not argv:
        raise ValidationError("missing command after --")
    for argument in argv:
        validate_argument(argument)
    executable = PureWindowsPath(argv[0]).name.lower()
    if argv[0].lower() != executable:
        raise ValidationError("executable paths are blocked; use the allowlisted command name")
    if executable not in ALLOWED_EXECUTABLES:
        raise ValidationError(
            f"executable {argv[0]!r} is not allowed; choose Python, pytest, or nvidia-smi"
        )
    arguments = argv[1:]
    if executable in {"py", "py.exe", "python", "python.exe"}:
        validate_python_arguments(arguments)
    elif executable in {"nvidia-smi", "nvidia-smi.exe"}:
        allowed_flags = {"-L", "--help", "--list-gpus"}
        for argument in arguments:
            if argument in allowed_flags:
                continue
            if argument.startswith(("--query-gpu=", "--format=")):
                continue
            raise ValidationError("nvidia-smi is limited to display/query flags")
    return argv


def encoded_powershell(script: str) -> str:
    return base64.b64encode(script.encode("utf-16le")).decode("ascii")


def remote_run_script(payload: dict[str, object]) -> str:
    payload_b64 = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    roots = "\n".join(f"  '{name}' = '{path}'" for name, path in REPO_ROOTS.items())
    return rf"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$payloadJson = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{payload_b64}'))
$payload = $payloadJson | ConvertFrom-Json
$repoRoots = @{{
{roots}
}}
$repoName = [string]$payload.repo
if (-not $repoRoots.ContainsKey($repoName)) {{ throw "Unregistered IIOT repository: $repoName" }}
$root = [IO.Path]::GetFullPath([string]$repoRoots[$repoName]).TrimEnd('\')
$cwd = $root
if ([string]$payload.cwd -and [string]$payload.cwd -ne '.') {{
  $cwd = [IO.Path]::GetFullPath((Join-Path $root ([string]$payload.cwd))).TrimEnd('\')
}}
$prefix = $root + '\'
if ($cwd -ne $root -and -not $cwd.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {{
  throw "Working directory escaped the selected repository"
}}
if (-not (Test-Path -LiteralPath $cwd -PathType Container)) {{ throw "Missing working directory: $cwd" }}
Set-Location -LiteralPath $cwd
$sourceRoot = Join-Path $root 'src'
if (Test-Path -LiteralPath $sourceRoot -PathType Container) {{
  if ($env:PYTHONPATH) {{ $env:PYTHONPATH = $sourceRoot + ';' + $env:PYTHONPATH }}
  else {{ $env:PYTHONPATH = $sourceRoot }}
}}
$argv = @($payload.argv | ForEach-Object {{ [string]$_ }})
$exe = $argv[0]
$processArgs = @()
if ($argv.Count -gt 1) {{ $processArgs = @($argv[1..($argv.Count - 1)]) }}
& $exe @processArgs
if ($null -eq $LASTEXITCODE) {{ exit 0 }}
exit $LASTEXITCODE
""".strip()


def remote_probe_script() -> str:
    root = REPO_ROOTS["iiot-ai-sensor-gateway"]
    return rf"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$root = '{root}'
$repoPresent = Test-Path -LiteralPath $root -PathType Container
if (-not $repoPresent) {{ throw "Missing synced IIOT repository: $root" }}
Set-Location -LiteralPath $root
$gpu = (& nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader | Select-Object -First 1)
$python = (& py -3.13 --version 2>&1 | Out-String).Trim()
$torch = (& py -3.13 scripts\probe_cuda.py 2>&1 | Out-String).Trim()
$gitBranch = ''
$gitDirtyCount = -1
if ($repoPresent) {{
  $status = @(& git -C $root status --short --branch)
  if ($status.Count -gt 0) {{ $gitBranch = [string]$status[0] }}
  $gitDirtyCount = [Math]::Max(0, $status.Count - 1)
}}
[ordered]@{{
  schema = 'iiot.devspace_laptop_probe.v1'
  host = $env:COMPUTERNAME
  user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
  repo = $root
  repo_present = $repoPresent
  gpu = [string]$gpu
  python = $python
  torch_probe = $torch
  git_branch = $gitBranch
  git_dirty_count = $gitDirtyCount
}} | ConvertTo-Json -Compress
""".strip()


def run_ssh(script: str, *, timeout: int) -> int:
    command = [
        "ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        SSH_HOST,
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-OutputFormat",
        "Text",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        encoded_powershell(script),
    ]
    try:
        completed = subprocess.run(command, check=False, timeout=timeout or None)
    except subprocess.TimeoutExpired:
        print(f"remote laptop command exceeded timeout={timeout}s", file=sys.stderr)
        return 124
    return completed.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    probe = subparsers.add_parser("probe", help="verify SSH, synced repo, Python, GPU, and CUDA")
    probe.add_argument("--timeout", type=int, default=60)
    run = subparsers.add_parser("run", help="run a scoped argv command in a registered laptop repo")
    run.add_argument("--repo", choices=sorted(REPO_ROOTS), required=True)
    run.add_argument("--cwd", default=".", help="repository-relative Windows working directory")
    run.add_argument("--timeout", type=int, default=0, help="seconds; 0 keeps long experiments unbounded")
    run.add_argument("argv", nargs=argparse.REMAINDER, help="command argv after --")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.action == "probe":
        return run_ssh(remote_probe_script(), timeout=args.timeout)
    command = list(args.argv)
    if command and command[0] == "--":
        command.pop(0)
    try:
        validate_relative_windows_path(args.cwd, label="Working directory")
        validate_argv(command)
    except ValidationError as error:
        print(f"blocked: {error}", file=sys.stderr)
        return 64
    payload: dict[str, object] = {"repo": args.repo, "cwd": args.cwd, "argv": command}
    return run_ssh(remote_run_script(payload), timeout=args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
