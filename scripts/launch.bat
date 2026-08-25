@echo off
title Chiky agente
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo [Chiky agente] Virtual environment not found.
    echo Please run setup first: powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
    echo.
    pause
    exit /b 1
)

echo.
echo   Starting Chiky agente...
echo.

".venv\Scripts\python.exe" scripts\launch.py %*

if errorlevel 1 (
    echo.
    echo [Chiky agente] Failed to start. See messages above.
    echo.
    pause
)
