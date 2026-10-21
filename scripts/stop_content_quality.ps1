$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Push-Location $projectRoot
try {
    & (Join-Path $projectRoot '.venv/Scripts/python.exe') -m scripts.content_quality_release_runtime stop
    if ($LASTEXITCODE -ne 0) { throw 'Owned stop failed; preserve runtime state.' }
} finally { Pop-Location }
