$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$workerPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
Push-Location $projectRoot
try {
    & $workerPython -m scripts.workspace_ingest_worker start
    if ($LASTEXITCODE -ne 0) { throw 'Dedicated Worker preparation failed; inspect independent logs.' }
} finally { Pop-Location }
