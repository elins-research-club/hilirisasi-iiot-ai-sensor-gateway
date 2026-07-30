#Requires -Version 5.1
<#
Safely align the Windows IIOT Syncthing ignore policy with the VPS and request
one local rescan. This script does not delete source, datasets, models, archives,
or Syncthing version history.
#>
param(
  [string]$WorkspaceRoot = "C:\vscode\IIOT-Project",
  [string]$SyncthingApi = "http://127.0.0.1:8384",
  [string]$FolderId = "vljbk-kftxq"
)
$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $WorkspaceRoot -PathType Container)) {
  throw "Workspace root not found: $WorkspaceRoot"
}

$ignorePath = Join-Path $WorkspaceRoot ".stignore"
$patterns = @(
  "// IIOT workspace: only reproducible build/cache artifacts.",
  "// Do not add data/, models/, archive/, .stversions/, or raw runtime evidence.",
  "",
  "(?d)**/__pycache__/",
  "(?d)**/*.pyc",
  "(?d)**/.pytest_cache/",
  "(?d)**/.ruff_cache/",
  "(?d)**/.pio/",
  "(?d)yolo_vision_gateway/build/",
  "(?d).code-review-graph/",
  "(?d)graphify-out/cache/"
)
$expected = ($patterns -join "`n") + "`n"
$current = if (Test-Path -LiteralPath $ignorePath) {
  [System.IO.File]::ReadAllText($ignorePath).Replace("`r`n", "`n")
} else { "" }

if ($current -ne $expected) {
  [System.IO.File]::WriteAllText($ignorePath, $expected, (New-Object System.Text.UTF8Encoding($false)))
  Write-Host "Updated $ignorePath"
} else {
  Write-Host "Ignore policy already current"
}

$configCandidates = @(
  (Join-Path $env:LOCALAPPDATA "Syncthing\config.xml"),
  (Join-Path $env:LOCALAPPDATA "Syncthing\config\config.xml")
)
$configPath = $configCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $configPath) {
  throw "Syncthing config.xml not found under LOCALAPPDATA; .stignore was updated, but automatic rescan was not requested."
}

[xml]$config = Get-Content -Raw -LiteralPath $configPath
$apiKey = [string]$config.configuration.gui.apikey
if (-not $apiKey) { throw "Syncthing API key not found in $configPath" }
$headers = @{ "X-API-Key" = $apiKey }

$folder = Invoke-RestMethod -Method Get -Headers $headers -Uri "$SyncthingApi/rest/config/folders/$FolderId"
if ($folder.path -ne $WorkspaceRoot) {
  throw "Folder $FolderId points to '$($folder.path)', not '$WorkspaceRoot'; refusing to rescan the wrong folder."
}
Invoke-RestMethod -Method Post -Headers $headers -Uri "$SyncthingApi/rest/db/scan?folder=$FolderId" | Out-Null
$status = Invoke-RestMethod -Method Get -Headers $headers -Uri "$SyncthingApi/rest/db/status?folder=$FolderId"
Write-Host ("Rescan requested: state={0} need={1} pullErrors={2}" -f $status.state, $status.needTotalItems, $status.pullErrors)
