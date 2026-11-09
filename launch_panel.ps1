# 面板桌面快捷方式的启动器：已运行就只开浏览器，没运行就先起服务再打开
$ErrorActionPreference = "Stop"
# 项目根 = 本脚本所在目录，换盘符/换机器都不用改
$root = $PSScriptRoot
$py   = Join-Path $root ".venv\Scripts\python.exe"
$port = 8501
$url  = "http://localhost:$port"
$log  = Join-Path $root "logs\panel_launch.out.log"
$err  = Join-Path $root "logs\panel_launch.err.log"

function Test-Port {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    return [bool]$c
}
function Test-Running {
    $p = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -like "*streamlit*" -and $_.CommandLine -like "*dashboard.py*" }
    return [bool]$p
}
function Fail($msg) {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($msg, "Agent 控制面板", "OK", "Error") | Out-Null
    exit 1
}

if (-not (Test-Path -LiteralPath (Join-Path $root "logs"))) { New-Item -ItemType Directory -Path (Join-Path $root "logs") -Force | Out-Null }

$started = $false
if (-not (Test-Port)) {
    if (-not (Test-Running)) {
        Start-Process -FilePath $py `
            -ArgumentList @("-m","streamlit","run","dashboard.py","--server.headless","true","--server.port","$port","--browser.gatherUsageStats","false") `
            -WorkingDirectory $root -WindowStyle Hidden `
            -RedirectStandardOutput $log -RedirectStandardError $err
        $started = $true
    }
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 700
        if (Test-Port) { break }
    }
}

if (-not (Test-Port)) {
    Fail("面板没能启动（端口 $port 未监听）。请查看日志：`n$log`n$err")
}

Start-Process $url