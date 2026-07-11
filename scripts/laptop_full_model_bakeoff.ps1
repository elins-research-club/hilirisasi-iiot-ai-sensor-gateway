#Requires -Version 5.1
param(
  [string]$PythonExe = "py",
  [string]$PythonVersion = "-3.13",
  [string]$Device = "auto",
  [int]$Epochs = 80,
  [int]$LstmEpochs = 40,
  [int]$Patience = 10,
  [int]$BatchSize = 256,
  [int]$Horizon = 5,
  [int]$PurgeGap = 5,
  [string]$Seeds = "42,43,44",
  [string]$Lanes = "gary,uci,fidas,sim",
  [switch]$SkipDownload,
  [switch]$SkipPublicPrepare,
  [switch]$SkipLstm,
  [switch]$Force,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$env:PYTHONPATH = (Join-Path $Root "src")

$runnerArgs = @(
  $PythonVersion,
  "scripts\laptop_bakeoff_runner.py",
  "--device", $Device,
  "--epochs", "$Epochs",
  "--lstm-epochs", "$LstmEpochs",
  "--patience", "$Patience",
  "--batch-size", "$BatchSize",
  "--horizon", "$Horizon",
  "--purge-gap", "$PurgeGap",
  "--seeds", $Seeds,
  "--lanes", $Lanes
)
if ($SkipDownload) { $runnerArgs += "--skip-download" }
if ($SkipPublicPrepare) { $runnerArgs += "--skip-public-prepare" }
if ($SkipLstm) { $runnerArgs += "--skip-lstm" }
if ($Force) { $runnerArgs += "--force" }
if ($DryRun) { $runnerArgs += "--dry-run" }

Write-Host ("Running: {0} {1}" -f $PythonExe, ($runnerArgs -join " ")) -ForegroundColor Cyan
& $PythonExe @runnerArgs
if ($LASTEXITCODE -ne 0) {
  throw "Bake-off runner failed with exit code $LASTEXITCODE"
}
