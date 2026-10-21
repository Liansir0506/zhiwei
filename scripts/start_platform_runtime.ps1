# Stage diagnostics wrap the same approved lifecycle functions, without publication.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
$pythonWindowless = Join-Path $projectRoot '.venv/Scripts/pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonWindowless)) { throw 'Windowless Python is required for background startup.' }
$inspectCode = @'
import json,httpx
from scripts import demo_release_runtime_r2 as r
from scripts import recover_recycled_runtime_state as recovery
r.install_adapter()
recovery.recover_record(r.owned.ROOT/'state.json',guard_port=8010)
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
    $output = & $pythonPath -c $inspectCode
    if ($LASTEXITCODE -ne 0) { throw 'Runtime identity inspection failed; preserve state.' }
    $inspection = ($output -join "`n") | ConvertFrom-Json
    if ($inspection.status -eq 'running') { return }
    $token = $null
    if ($inspection.status -ne 'starting') {
        & $pythonPath -m scripts.demo_release_runtime_r2 stop
        if ($LASTEXITCODE -ne 0) { throw 'Owned cleanup failed; preserve state.' }
        $token = [guid]::NewGuid().ToString('N')
        $desktopShell = New-Object -ComObject Shell.Application
        $desktopShell.ShellExecute($pythonWindowless, "-m scripts.platform_runtime_bootstrap --token $token", $projectRoot, 'open', 0)
    }
    # Full publication snapshot verification precedes the existing 150s app warmup.
    $deadline = (Get-Date).AddSeconds(360)
    do {
        Start-Sleep -Seconds 2
        $bootstrapPath = Join-Path $projectRoot '.runtime/platform-entry/bootstrap.json'
        if ($token -and (Test-Path -LiteralPath $bootstrapPath)) {
            $bootstrap = Get-Content -LiteralPath $bootstrapPath -Raw | ConvertFrom-Json
            if ($bootstrap.token -eq $token -and $bootstrap.status -eq 'failed') {
                throw "Owned startup failed at $($bootstrap.phase): $($bootstrap.error_type). See bootstrap.json."
            }
        }
        $output = & $pythonPath -c $inspectCode
        if ($LASTEXITCODE -ne 0) { throw 'Runtime identity inspection failed; preserve state.' }
        $inspection = ($output -join "`n") | ConvertFrom-Json
        if ($inspection.status -eq 'running') { return }
        if ((Get-Date) -ge $deadline) { throw 'Owned startup timed out. See bootstrap phase and server.log; preserve state.' }
    } while ($true)
} finally { Pop-Location }
