@echo off
title GCC Job Radar Extension Bridge
echo ======================================================================
echo    Starting GCC Job Radar Chrome Extension Bridge Server
echo ======================================================================
echo.
cd /d "%~dp0"

REM Prefer uv run python if available, fallback to python
where uv >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [*] Running via uv...
    uv run python tools/extension_bridge.py
) else (
    echo [*] Running via python...
    python tools/extension_bridge.py
)

pause
