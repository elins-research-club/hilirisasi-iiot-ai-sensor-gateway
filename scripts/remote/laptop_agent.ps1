#Requires -Version 5.1
# Hermes remote job agent for Windows laptop (Tailscale/Syncthing shared repo).
# Polls jobs/inbox for *.job.json, runs command, writes jobs/outbox result.
param(
  [string]$RepoRoot = "",
  [int]$PollSeconds = 5,
  [switch]$Once
)
$ErrorActionPreference = "Stop"
if (-not $RepoRoot) { $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path }
Set-Location $RepoRoot
$inbox = Join-Path $RepoRoot "jobs\inbox"
$running = Join-Path $RepoRoot "jobs\running"
$outbox = Join-Path $RepoRoot "jobs\outbox"
$logs = Join-Path $RepoRoot "jobs\logs"
foreach ($d in @($inbox,$running,$outbox,$logs)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
$env:PYTHONPATH = (Join-Path $RepoRoot "src")
function Write-Result($job, $status, $exitCode, $logPath) {
  $result = [ordered]@{
    schema = "iiot.remote_job_result.v1"
    job_id = $job.job_id
    status = $status
    exit_code = $exitCode
    finished_at = (Get-Date).ToUniversalTime().ToString("o")
    host = $env:COMPUTERNAME
    log_path = $logPath
    command = $job.command
  }
  $out = Join-Path $outbox ($job.job_id + ".result.json")
  ($result | ConvertTo-Json -Depth 6) | Set-Content -Path $out -Encoding UTF8
}
Write-Host "Laptop agent watching $inbox"
while ($true) {
  $jobs = Get-ChildItem -Path $inbox -Filter "*.job.json" -ErrorAction SilentlyContinue | Sort-Object Name
  foreach ($file in $jobs) {
    $job = $null
    $dest = $null
    try {
      $raw = Get-Content -Raw -Path $file.FullName
      $job = $raw | ConvertFrom-Json
      $dest = Join-Path $running $file.Name
      Move-Item -Force $file.FullName $dest
      $logPath = Join-Path $logs ($job.job_id + ".log")
      Write-Host "RUN $($job.job_id): $($job.command)"
      $cmd = $job.command
      if ($job.workdir) { Set-Location $job.workdir } else { Set-Location $RepoRoot }
      $p = Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", $cmd) -NoNewWindow -Wait -PassThru -RedirectStandardOutput $logPath -RedirectStandardError ($logPath + ".err")
      Get-Content ($logPath + ".err") -ErrorAction SilentlyContinue | Add-Content $logPath
      Remove-Item ($logPath + ".err") -ErrorAction SilentlyContinue
      $status = if ($p.ExitCode -eq 0) { "completed" } else { "failed" }
      Write-Result $job $status $p.ExitCode $logPath
      Remove-Item -Force $dest -ErrorAction SilentlyContinue
      Write-Host "DONE $($job.job_id) $status exit=$($p.ExitCode)"
    } catch {
      Write-Host "ERROR $($file.Name): $_"
      try {
        if (-not $job) {
          $fallbackId = $file.Name -replace '\.job\.json$', ''
          $job = @{ job_id = $fallbackId; command = "unknown" }
        }
        Write-Result $job "failed" 1 ""
        if ($dest) { Remove-Item -Force $dest -ErrorAction SilentlyContinue }
      } catch {}
      Set-Location $RepoRoot
    }
  }
  if ($Once) { break }
  Start-Sleep -Seconds $PollSeconds
}
