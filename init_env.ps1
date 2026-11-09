# init_env.ps1
Write-Host "===== Start Agent Project Env Init =====" -ForegroundColor Cyan

$VENV_DIR = ".venv"

if (Test-Path $VENV_DIR) {
    Write-Host ".venv already exists, skip creation." -ForegroundColor Yellow
}
else {
    Write-Host "Creating virtual environment..." -ForegroundColor Green
    python -m venv $VENV_DIR
    if (-not (Test-Path "$VENV_DIR\Scripts\activate.ps1")) {
        py -m venv $VENV_DIR
    }
}

$activatePath = "$VENV_DIR\Scripts\Activate.ps1"
if (Test-Path $activatePath) {
    . $activatePath
    Write-Host "✅ Virtual environment activated" -ForegroundColor Green
}
else {
    Write-Host "❌ Cannot find activate script" -ForegroundColor Red
    exit 1
}

Write-Host "Upgrading pip..." -ForegroundColor Green
python -m pip install --upgrade pip

if (Test-Path "requirements.txt") {
    Write-Host "Install packages from requirements.txt ..." -ForegroundColor Green
    pip install -r requirements.txt
}
else {
    Write-Host "❌ requirements.txt not found!" -ForegroundColor Red
    exit 1
}

Write-Host "`n===== All Done =====" -ForegroundColor Cyan
Write-Host "To activate env next time:" -ForegroundColor White
Write-Host '.\.venv\Scripts\Activate.ps1' -ForegroundColor Yellow
