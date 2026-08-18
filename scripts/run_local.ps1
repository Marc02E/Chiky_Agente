$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

$py = '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw 'No existe .venv. Ejecuta .\scripts\setup_windows.ps1 primero.' }

$env:PYTHONPATH = Join-Path (Get-Location) 'src'
& $py -m alembic upgrade head
& $py -m uvicorn personal_ai_secretary.api.app:app --host 127.0.0.1 --port 8000
