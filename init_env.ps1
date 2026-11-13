# init_env.ps1 —— 一键初始化 Agent Room 运行环境
# 用法：powershell -ExecutionPolicy Bypass -File .\init_env.ps1
# 说明：只做“装好”，不做“常驻”。常驻（计划任务）见 docs/DEPLOY.md 第 7 节。
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot          # 项目根 = 本脚本所在目录，换盘符/换机器都不改
Set-Location $root

Write-Host "===== Agent Room 环境初始化 =====" -ForegroundColor Cyan
Write-Host "项目根: $root" -ForegroundColor DarkGray

# ---- 1) 检查 Python ----
$ver = (& python --version 2>&1)
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ 找不到 python，请先安装 Python 3.11+ 并加入 PATH" -ForegroundColor Red
    exit 1
}
Write-Host "Python: $ver" -ForegroundColor Green

# ---- 2) 建/复用 venv ----
$venv = Join-Path $root ".venv"
$py = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $py)) {
    Write-Host "创建虚拟环境..." -ForegroundColor Green
    & python -m venv $venv
    if (-not (Test-Path -LiteralPath $py)) { & py -m venv $venv }
}
if (-not (Test-Path -LiteralPath $py)) {
    Write-Host "❌ venv 创建失败（python -m venv 和 py -m venv 都不可用）" -ForegroundColor Red
    exit 1
}

# ---- 3) 装依赖 ----
Write-Host "升级 pip..." -ForegroundColor Green
& $py -m pip install --upgrade pip
if (-not (Test-Path -LiteralPath (Join-Path $root "requirements.txt"))) {
    Write-Host "❌ requirements.txt 不存在" -ForegroundColor Red
    exit 1
}
Write-Host "安装依赖（首次会拉 torch，几 GB，慢是正常的）..." -ForegroundColor Green
& $py -m pip install -r (Join-Path $root "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ 依赖安装失败，看上面 pip 的报错" -ForegroundColor Red
    exit 1
}

# ---- 4) 生成运行配置 ----
$cfg = Join-Path $root "config\config.toml"
$example = Join-Path $root "config\config.example.toml"
if (-not (Test-Path -LiteralPath $cfg)) {
    Copy-Item -LiteralPath $example -Destination $cfg
    Write-Host "已生成 config\config.toml（来自示例）" -ForegroundColor Yellow
} else {
    Write-Host "config\config.toml 已存在，保留不动" -ForegroundColor DarkGray
}

# ---- 5) 建运行目录 ----
foreach ($d in @("logs", "sandbox", "agent_memory_db")) {
    $p = Join-Path $root $d
    if (-not (Test-Path -LiteralPath $p)) {
        New-Item -ItemType Directory -Path $p | Out-Null
        Write-Host "创建目录: $d" -ForegroundColor DarkGray
    }
}

# ---- 6) 探 Ollama ----
Write-Host "检查 Ollama（http://localhost:11434）..." -ForegroundColor Green
try {
    $resp = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -TimeoutSec 5 -UseBasicParsing
    $models = ($resp.Content | ConvertFrom-Json).models
    Write-Host "Ollama: 可达" -ForegroundColor Green
    if ($models) {
        $names = $models | ForEach-Object { $_.name }
        Write-Host ("已装模型: " + ($names -join ", ")) -ForegroundColor DarkGray
    } else {
        Write-Host "Ollama 在跑，但还没有任何模型——先 ollama pull <模型名>" -ForegroundColor Yellow
    }
} catch {
    Write-Host "⚠ Ollama 不可达。装好 Ollama（https://ollama.com/download）后再跑面板" -ForegroundColor Yellow
}

Write-Host "`n===== 完成 =====" -ForegroundColor Cyan
Write-Host "下一步："
Write-Host "  1) 打开 config\config.toml，按这台机器改模型名与书库/资料库路径"
Write-Host "  2) ollama pull <模型名>（配置里写的那个）"
Write-Host "  3) .\启动面板.bat   →   http://localhost:8501"
Write-Host "  4) 要开机自启，看 docs\DEPLOY.md 第 7 节（计划任务）"
