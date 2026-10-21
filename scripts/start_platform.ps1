# Unified preparation of the approved local entry and persistent observation.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$env:PYTHONIOENCODING = 'utf-8'
Push-Location $projectRoot
try {
    & (Join-Path $projectRoot '.venv/Scripts/python.exe') -m scripts.platform_entry prepare
    if ($LASTEXITCODE -ne 0) { throw 'Platform preparation failed. See .runtime/platform-entry/status.html.' }
    & (Join-Path $PSScriptRoot 'start_workspace_worker_background.ps1')
} finally { Pop-Location }
