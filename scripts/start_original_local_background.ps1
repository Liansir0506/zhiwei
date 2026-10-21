$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
$statePath = Join-Path $projectRoot '.runtime/original-local/state.json'
Push-Location $projectRoot
try {
    # Reuse an attested running entry; never start a second copy or publish.
    $runtimeInspectionOutput = & $pythonPath -m scripts.original_local_runtime status
    $runtimeInspectionExitCode = $LASTEXITCODE
    $runtimeInspectionOutput | Write-Output
    if ($runtimeInspectionExitCode -eq 0) {
        $runtimeInspection = ($runtimeInspectionOutput -join "`n") | ConvertFrom-Json
        if ($runtimeInspection.status -eq 'running') { return }
    }
    # This existing command refuses to terminate any process that fails ownership checks.
    & $pythonPath -m scripts.original_local_runtime stop
    if ($LASTEXITCODE -ne 0) { throw 'Owned runtime cleanup failed; preserve the state.' }
    # A desktop shell parent isolates the service from the coding host's console/job.
    # show=0 keeps both launcher and service hidden. Existing runtime verifies all bindings.
    $desktopRuntimeShell = New-Object -ComObject Shell.Application
    $desktopRuntimeShell.ShellExecute($pythonPath, '-m scripts.original_local_runtime start', $projectRoot, 'open', 0)
    $startupDeadline = (Get-Date).AddSeconds(150)
    do {
        if (Test-Path -LiteralPath $statePath) {
            $ownedRuntimeState = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
            if ($ownedRuntimeState.status -eq 'running') { break }
        }
        if ((Get-Date) -ge $startupDeadline) { throw 'Owned runtime startup timed out; inspect server.log.' }
        Start-Sleep -Seconds 1
    } while ($true)
    & $pythonPath -m scripts.original_local_runtime status
    if ($LASTEXITCODE -ne 0) { throw 'Owned runtime identity check failed.' }
} finally { Pop-Location }
