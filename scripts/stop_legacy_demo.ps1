$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    & (Join-Path (Split-Path $PSScriptRoot -Parent) '.venv/Scripts/python.exe') -m scripts.legacy_demo_runtime stop
    if ($LASTEXITCODE -ne 0) { throw 'Isolated demo stop failed.' }
} finally { Pop-Location }
