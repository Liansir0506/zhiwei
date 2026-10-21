$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Push-Location $projectRoot
try {
    & (Join-Path $projectRoot '.venv/Scripts/python.exe') -m scripts.original_local_runtime stop
    if ($LASTEXITCODE -ne 0) { throw 'Original local entry stop failed; preserve the recovery state.' }
} finally { Pop-Location }
