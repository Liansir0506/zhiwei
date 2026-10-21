# 本地演示平台：一键启动 / 打开（供桌面“启动运维平台.bat”调用）
# 行为：
#   未启动            -> 启动并等待就绪，然后打开登录页
#   运行中（新版本）   -> 直接打开登录页
#   运行中（旧版本）   -> 自动先停再启（加载新功能），然后打开登录页
# 用法（一般由桌面 bat 调用）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\launch_platform.ps1 [-Check]
#   -Check 仅探测状态并输出（health/dashboard/state），不执行任何动作
# 运行记录：.runtime\launch-platform.log（出问题时把这个文件发给维护者）
param([switch]$Check)

$ErrorActionPreference = 'Stop'
$scriptRoot  = $PSScriptRoot
$root        = Split-Path -Parent $scriptRoot
$baseUrl     = 'http://127.0.0.1:8010'
$loginPage   = Join-Path $root '.runtime\original-local\open-platform.html'
$startScript = Join-Path $scriptRoot 'start_platform.ps1'
$stopScript  = Join-Path $scriptRoot 'stop_platform.ps1'
$logPath     = Join-Path $root '.runtime\launch-platform.log'

function Log([string]$Message) {
    Write-Host $Message
    try { [System.IO.File]::AppendAllText($logPath, $Message + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false)) } catch { }
}

function Get-HttpCode([string]$Url) {
    $out = & curl.exe -s -m 5 --noproxy '*' -o NUL -w '%{http_code}' $Url 2>$null
    $code = ("$out" | Out-String).Trim()
    if ($code -match '^[0-9]{3}$') { return $code }
    return '000'
}

function Invoke-ChildScript([string]$ScriptPath) {
    # 独立子进程执行；stdout/stderr 逐行转发到控制台并写入日志，函数只返回退出码（int）
    # 注意1：绝不能让子进程输出混入返回值（否则退出码变成数组，判断必错）
    # 注意2：统一子进程输出为 UTF-8 并按 UTF-8 读取（python 经 PYTHONIOENCODING，读取端受 Console.OutputEncoding 控制），
    #        避免中文输出被按 GBK 误解码产生乱码（实测修复）
    Log ('---- 执行 ' + (Split-Path -Leaf $ScriptPath) + ' ----')
    $prevEnc = [Console]::OutputEncoding
    $prevPy  = $env:PYTHONIOENCODING
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $env:PYTHONIOENCODING = 'utf-8'
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ScriptPath 2>&1 | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) { $text = $_.Exception.Message }
            else { $text = ($_ | Out-String).TrimEnd() }
            Write-Host $text
            Log $text
        }
        $code = $LASTEXITCODE
    } finally {
        [Console]::OutputEncoding = $prevEnc
        $env:PYTHONIOENCODING = $prevPy
    }
    Log ('---- ' + (Split-Path -Leaf $ScriptPath) + ' 结束：退出码 ' + $code + ' ----')
    return $code
}

function Open-Console {
    if (Test-Path $loginPage) {
        Start-Process $loginPage | Out-Null
        Log '已打开登录页；点页面上的「进入平台」即可（Key 每次启动轮换，请勿使用旧标签页）。'
    } else {
        Log "未找到登录页：$loginPage"
    }
}

Log ("[" + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + "] 启动器运行：Check=" + $Check)

# ---- 1) 探测当前状态 ----
# dashboard/summary 无凭据探测：404=运行的是旧版本（无看板路由），401/其它=新版本在运行
$health    = Get-HttpCode "$baseUrl/health"
$dashboard = '000'
# 版本指纹：/admin/version-workspace/summary 为 r2 优化轮新增路由；404 表示运行的是
# 更早的代码，需要停旧启新以加载修复与 r2 修订（401/200 视为最新版）。
$fingerprint = '000'
if ($health -eq '200') {
    $dashboard = Get-HttpCode "$baseUrl/dashboard/summary"
    $fingerprint = Get-HttpCode "$baseUrl/admin/version-workspace/summary"
}

$state = 'stopped'
if ($health -eq '200') {
    if ($dashboard -eq '404' -or $fingerprint -eq '404') { $state = 'stale' } else { $state = 'running' }
}

if ($Check) {
    Log "health=$health dashboard=$dashboard fingerprint=$fingerprint state=$state"
    exit 0
}

# ---- 2) 已在运行（新版本）-> 直接打开 ----
if ($state -eq 'running') {
    Log '平台已在运行。'
    Open-Console
    exit 0
}

# ---- 3) 旧版本在运行 -> 先停止 ----
if ($state -eq 'stale') {
    Log '检测到平台正在运行改动前的旧版本（缺少本轮修复与 r2 修订）。'
    Log '将先停止再启动以加载新版本，全程约 1～2 分钟…'
    $stopCode = Invoke-ChildScript $stopScript
    # 客观判定：不只看退出码，直接验证 8010 是否已释放
    Start-Sleep -Seconds 2
    $still = Get-HttpCode "$baseUrl/health"
    if ($still -eq '200') {
        Log ''
        Log "[错误] 停止未生效：停止脚本已结束（退出码 $stopCode），但平台仍在 8010 响应。"
        Log '最常见原因：旧实例是以管理员权限启动的，而本窗口权限不足，无法停止它。'
        Log '请尝试：关闭本窗口 → 右键“启动运维平台.bat” → “以管理员身份运行”。'
        Log '若仍失败：把 .runtime\launch-platform.log 这个文件发给维护者。'
        exit 1
    }
    if ($stopCode -ne 0) {
        Log "[警告] 停止脚本退出码为 $stopCode，但 8010 已释放，继续启动。"
    }
    Log '旧版本已停止。'
} else {
    Log '平台未在运行。'
}

# ---- 4) 启动 ----
Log '正在启动平台（首次启动需要预热，约 30～90 秒，请稍候）…'
$startCode = Invoke-ChildScript $startScript
if ($startCode -ne 0) {
    Log "[错误] 启动失败（退出码 $startCode）。请查看上方输出与 .runtime\platform-entry\status.html；也可以把 .runtime\launch-platform.log 发给维护者。"
    exit 1
}

# 启动脚本内部通常已等到就绪；这里再轮询一次双保险（最多 4 分钟）
$deadline = (Get-Date).AddSeconds(240)
$ready = $false
while ((Get-Date) -lt $deadline) {
    if ((Get-HttpCode "$baseUrl/health") -eq '200') { $ready = $true; break }
    Start-Sleep -Seconds 3
}
if (-not $ready) {
    Log '[错误] 启动脚本已结束，但健康检查在 4 分钟内未通过：请查看上方输出。'
    exit 1
}

# ---- 5) 打开登录页 ----
Log "平台已就绪：$baseUrl"
Open-Console
exit 0
