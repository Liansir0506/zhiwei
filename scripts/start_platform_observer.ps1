param([Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{32}$')][string]$Instance)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $projectRoot '.venv/Scripts/pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Windowless Python is required for background observation.' }
$desktopShell = New-Object -ComObject Shell.Application
$desktopShell.ShellExecute($pythonPath, "-m scripts.platform_entry watch --instance $Instance", $projectRoot, 'open', 0)
