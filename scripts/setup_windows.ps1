$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

Write-Host '=== personal_ai_secretary — Windows setup ===' -ForegroundColor Cyan

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw 'Python no está disponible en PATH. Instala Python 3.13+ y vuelve a ejecutar este script.'
}

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    python -m venv .venv
}

& .venv\Scripts\python.exe -m pip install --upgrade pip
& .venv\Scripts\python.exe -m pip install -e '.[dev]'

if (-not (Test-Path '.env')) {
@'
APP_ENV=development
AI_PROVIDER=deterministic
JWT_REQUIRED=false
JWT_SECRET=change-this-local-development-secret
DATABASE_URL=sqlite+aiosqlite:///./personal_ai_secretary.db
LOG_LEVEL=INFO
'@ | Set-Content -Path '.env' -Encoding UTF8
}

& .venv\Scripts\python.exe -m alembic upgrade head
Write-Host 'Setup completado. Ejecuta .\scripts\verify_baseline.ps1 para validar los gates.' -ForegroundColor Green
