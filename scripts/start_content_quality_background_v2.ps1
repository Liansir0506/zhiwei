# Read-only inspection never takes the lifecycle lock held by startup.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
$pythonWindowless = Join-Path $projectRoot '.venv/Scripts/pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonWindowless)) { throw 'Windowless Python is required for background startup.' }
$inspectCode = @'
import json,httpx
from scripts import content_quality_release_runtime as r
r.install_adapter()
try:
    result=r.owned.inspect()
except httpx.TransportError:
    state=r.owned.load_state()
    if not state or state.get('status')!='starting' or r.owned.owned_process(state) is None:
        raise
    result={'status':'starting'}
print(json.dumps(result))
'@
Push-Location $projectRoot
try {
    $inspectionOutput = & $pythonPath -c $inspectCode
    if ($LASTEXITCODE -ne 0) { throw 'Runtime identity inspection failed.' }
    $inspection = ($inspectionOutput -join "`n") | ConvertFrom-Json
    if ($inspection.status -eq 'running') { $inspectionOutput | Write-Output; return }
    if ($inspection.status -ne 'starting') {
        & $pythonPath -m scripts.content_quality_release_runtime stop
        if ($LASTEXITCODE -ne 0) { throw 'Owned cleanup failed; preserve state.' }
        $desktopShell = New-Object -ComObject Shell.Application
        $desktopShell.ShellExecute($pythonWindowless, '-m scripts.content_quality_release_runtime start', $projectRoot, 'open', 0)
    }
    $deadline = (Get-Date).AddSeconds(180)
    do {
        Start-Sleep -Seconds 2
        $inspectionOutput = & $pythonPath -c $inspectCode
        if ($LASTEXITCODE -ne 0) { throw 'Runtime identity inspection failed; preserve state.' }
        $inspection = ($inspectionOutput -join "`n") | ConvertFrom-Json
        if ($inspection.status -eq 'running') { $inspectionOutput | Write-Output; return }
        if ((Get-Date) -ge $deadline) { throw 'Approved runtime startup timed out.' }
    } while ($true)
} finally { Pop-Location }
