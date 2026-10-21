param([int]$Port = 18011)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    & (Join-Path (Split-Path $PSScriptRoot -Parent) '.venv/Scripts/python.exe') -m scripts.legacy_demo_runtime start --port $Port
    if ($LASTEXITCODE -ne 0) { throw 'Isolated demo start failed.' }
} finally { Pop-Location }
