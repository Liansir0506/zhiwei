$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
Push-Location $projectRoot
try {
    $inspectionOutput = & $pythonPath -m scripts.content_quality_release_runtime status
    $inspectionExit = $LASTEXITCODE
    $inspectionOutput | Write-Output
    if ($inspectionExit -ne 0) { throw 'Runtime binding inspection failed; preserve state.' }
    $inspection = ($inspectionOutput -join "`n") | ConvertFrom-Json
    if ($inspection.status -eq 'running') { return }
    & $pythonPath -m scripts.content_quality_release_runtime stop
    if ($LASTEXITCODE -ne 0) { throw 'Owned cleanup failed.' }
    $desktopShell = New-Object -ComObject Shell.Application
    $desktopShell.ShellExecute($pythonPath, '-m scripts.content_quality_release_runtime start', $projectRoot, 'open', 0)
    $deadline = (Get-Date).AddSeconds(150)
    do {
        Start-Sleep -Seconds 2
        $inspectionOutput = & $pythonPath -m scripts.content_quality_release_runtime status
        if ($LASTEXITCODE -ne 0) { throw 'Runtime identity inspection failed.' }
        $inspection = ($inspectionOutput -join "`n") | ConvertFrom-Json
        if ($inspection.status -eq 'running') { $inspectionOutput | Write-Output; return }
        if ((Get-Date) -ge $deadline) { throw 'Approved runtime startup timed out.' }
    } while ($true)
} finally { Pop-Location }
