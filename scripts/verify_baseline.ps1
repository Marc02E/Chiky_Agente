$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

$py = '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw 'No existe .venv. Ejecuta .\scripts\setup_windows.ps1 primero.' }

Write-Host '=== pytest + coverage ===' -ForegroundColor Cyan
& $py -m pytest tests --cov=personal_ai_secretary --cov-report=term-missing --cov-fail-under=94 -q

Write-Host '=== mypy ===' -ForegroundColor Cyan
& $py -m mypy src/personal_ai_secretary --ignore-missing-imports

Write-Host '=== ruff ===' -ForegroundColor Cyan
& $py -m ruff check src tests

Write-Host '=== alembic ===' -ForegroundColor Cyan
& $py -m alembic heads

Write-Host '=== baseline verification complete ===' -ForegroundColor Green
