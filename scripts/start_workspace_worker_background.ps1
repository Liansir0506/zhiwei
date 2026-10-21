# Use GUI-subsystem Python outside the calling terminal/job. Keep worker bindings intact.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
$pythonWindowless = Join-Path $projectRoot '.venv/Scripts/pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonWindowless)) { throw 'Windowless Python is required for background Worker startup.' }
Push-Location $projectRoot
try {
    $inspectCode = @'
import json
from scripts import workspace_ingest_worker as w
state=w.load_state(); process=w.owned(state)
print(json.dumps({'running':process is not None,'status':state.get('status') if state else 'stopped'}))
'@
    $status = (& $pythonPath -c $inspectCode | ConvertFrom-Json)
    if ($LASTEXITCODE -ne 0) { throw 'Worker ownership inspection failed; preserve state.' }
    if ($status.running -and $status.status -eq 'ready') { return }
    & $pythonPath -m scripts.workspace_ingest_worker stop
    if ($LASTEXITCODE -ne 0) { throw 'Owned Worker cleanup failed; preserve state.' }
    $desktopShell = New-Object -ComObject Shell.Application
    $desktopShell.ShellExecute($pythonWindowless, '-m scripts.workspace_ingest_worker start', $projectRoot, 'open', 0)
    $deadline = (Get-Date).AddSeconds(150)
    do {
        Start-Sleep -Seconds 2
        $status = (& $pythonPath -c $inspectCode | ConvertFrom-Json)
        if ($LASTEXITCODE -ne 0) { throw 'Worker ownership inspection failed; preserve state.' }
        if ($status.running -and $status.status -eq 'ready') { return }
        if ((Get-Date) -ge $deadline) { throw 'Windowless Worker startup timed out; inspect worker.error.log.' }
    } while ($true)
} finally { Pop-Location }
