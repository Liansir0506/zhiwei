# Preferred entry for the training UI; historical bound launcher remains unchanged.
$ErrorActionPreference = 'Stop'
$businessProjectRoot = Split-Path $PSScriptRoot -Parent
$businessPython = Join-Path $businessProjectRoot '.venv/Scripts/python.exe'
$businessPythonHidden = Join-Path $businessProjectRoot '.venv/Scripts/pythonw.exe'
Push-Location $businessProjectRoot
try {
    $businessMode = & $businessPython -m scripts.training_business_runtime status | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'Inspect the owned business entry before restarting.' }
    if ($businessMode.status -eq 'running' -and $businessMode.presentation_mode -eq 'training_workspace') { return }
    if ($businessMode.status -notin @('stopped', 'running')) { throw 'Stop the owned stale business entry first.' }
    $businessShell = New-Object -ComObject Shell.Application
    $businessLaunchAt = (Get-Date).ToUniversalTime()
    $businessShell.ShellExecute($businessPythonHidden, '-m scripts.training_business_bootstrap', $businessProjectRoot, 'open', 0)
    $businessDeadline = (Get-Date).AddSeconds(270)
    do {
        Start-Sleep -Seconds 2
        $businessMode = & $businessPython -m scripts.training_business_runtime status | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0) { throw 'Owned business status check failed; inspect its logs.' }
        if ($businessMode.status -eq 'running') { return }
        $businessStartupPath = Join-Path $businessProjectRoot '.runtime/readonly-workspace/training-startup.json'
        if (Test-Path -LiteralPath $businessStartupPath) {
            $businessStartup = Get-Content -LiteralPath $businessStartupPath -Raw | ConvertFrom-Json
            if ($businessStartup.status -eq 'failed' -and [datetimeoffset]::Parse($businessStartup.at_utc).UtcDateTime -ge $businessLaunchAt) { throw 'Business startup failed; inspect its dedicated logs.' }
        }
        if ((Get-Date) -ge $businessDeadline) { throw 'Business startup timed out; inspect its dedicated logs.' }
    } while ($true)
} finally { Pop-Location }
