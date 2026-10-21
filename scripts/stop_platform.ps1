# Stop only the owned observer and application; keep local storage available.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
Push-Location $projectRoot
try {
    & $pythonPath -m scripts.workspace_ingest_worker stop
    if ($LASTEXITCODE -ne 0) { throw 'Ingest Worker ownership check failed; preserve state.' }
    & $pythonPath -m scripts.platform_entry stop-observer
    if ($LASTEXITCODE -ne 0) { throw 'Observer ownership check failed; preserve state.' }
    # r2 修订后：停止器唯一指向 r2 运行时；r2 链兼容识别 r1 壳实例（受控迁移），
    # 历史入口的停止器不再适用。
    & $pythonPath -m scripts.demo_release_runtime_r2 stop
    if ($LASTEXITCODE -ne 0) { throw 'Owned stop failed; preserve runtime state.' }
} finally { Pop-Location }
