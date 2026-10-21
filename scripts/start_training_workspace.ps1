$ErrorActionPreference = 'Stop'
$trainingProjectRoot = Split-Path $PSScriptRoot -Parent
$trainingPython = Join-Path $trainingProjectRoot '.venv/Scripts/python.exe'
$trainingPythonHidden = Join-Path $trainingProjectRoot '.venv/Scripts/pythonw.exe'
if (-not (Test-Path -LiteralPath $trainingPythonHidden)) { throw 'Windowless Python is required.' }
Push-Location $trainingProjectRoot
try {
    $trainingStatus = & $trainingPython -m scripts.training_exercise_runtime status | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'Owned training runtime inspection failed.' }
    if ($trainingStatus.status -eq 'running' -and $trainingStatus.training_module) { return }
    $trainingStartedAt = (Get-Date).ToUniversalTime()
    $trainingShell = New-Object -ComObject Shell.Application
    $trainingShell.ShellExecute($trainingPythonHidden, '-m scripts.training_exercise_bootstrap', $trainingProjectRoot, 'open', 0)
    $trainingDeadline = (Get-Date).AddSeconds(270)
    do {
        Start-Sleep -Seconds 2
        $trainingStatus = & $trainingPython -m scripts.training_exercise_runtime status | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0) { throw 'Owned runtime inspection failed; preserve process state.' }
        if ($trainingStatus.status -eq 'running' -and $trainingStatus.training_module) { return }
        $trainingStatePath = Join-Path $trainingProjectRoot '.runtime/readonly-workspace/exercise-startup.json'
        if (Test-Path -LiteralPath $trainingStatePath) {
            $trainingStartup = Get-Content -LiteralPath $trainingStatePath -Raw | ConvertFrom-Json
            if ($trainingStartup.status -eq 'failed' -and [datetimeoffset]::Parse($trainingStartup.at_utc).UtcDateTime -ge $trainingStartedAt) { throw 'Training startup failed; inspect its sanitized startup state.' }
        }
        if ((Get-Date) -ge $trainingDeadline) { throw 'Training startup timed out; preserve state.' }
    } while ($true)
} finally { Pop-Location }
