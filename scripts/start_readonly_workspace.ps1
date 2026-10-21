# Launch outside the terminal job; fixed loopback, exact approved domain, no paid model.
$ErrorActionPreference = 'Stop'
$workspaceRoot = Split-Path $PSScriptRoot -Parent
$workspacePython = Join-Path $workspaceRoot '.venv/Scripts/python.exe'
$workspacePythonWindowless = Join-Path $workspaceRoot '.venv/Scripts/pythonw.exe'
if (-not (Test-Path -LiteralPath $workspacePythonWindowless)) { throw 'Windowless Python is required for background startup.' }
Push-Location $workspaceRoot
try {
    $inspection = (& $workspacePython -m scripts.readonly_workspace_runtime status | ConvertFrom-Json)
    if ($LASTEXITCODE -ne 0) { throw 'Owned inspection failed; preserve state.' }
    if ($inspection.status -eq 'running') { return }
    if ($inspection.status -ne 'stopped') { throw 'Stop the owned stale entry before restarting.' }
    $desktopShell = New-Object -ComObject Shell.Application
    $launchAt = (Get-Date).ToUniversalTime()
    $desktopShell.ShellExecute($workspacePythonWindowless, '-m scripts.readonly_workspace_bootstrap prepare-start', $workspaceRoot, 'open', 0)
    $deadline = (Get-Date).AddSeconds(270)
    do {
        Start-Sleep -Seconds 2
        $inspection = (& $workspacePython -m scripts.readonly_workspace_runtime status | ConvertFrom-Json)
        if ($LASTEXITCODE -ne 0) { throw 'Owned inspection failed; preserve state.' }
        if ($inspection.status -eq 'running') { return }
        $failurePath = Join-Path $workspaceRoot '.runtime/readonly-workspace/failure.json'
        if (Test-Path -LiteralPath $failurePath) {
            $failure = Get-Content -LiteralPath $failurePath -Raw | ConvertFrom-Json
            if ([datetimeoffset]::Parse($failure.at).UtcDateTime -ge $launchAt) { throw "Read-only startup failed ($($failure.error_type)); inspect bootstrap.json." }
        }
        if ((Get-Date) -ge $deadline) { throw 'Read-only workspace startup timed out; inspect dedicated logs.' }
    } while ($true)
} finally { Pop-Location }
