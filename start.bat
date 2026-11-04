@echo off
title LucyLab Story Studio
echo.
echo ========================================
echo   LucyLab Story Studio
echo   http://localhost:9000
echo ========================================
echo.

:: ── Check config.json exists ──────────────────────────────────────
if not exist "%~dp0config.json" (
    echo ERROR: config.json not found.
    echo   Run install.bat first, then edit config.json with your settings.
    pause
    exit /b 1
)

:: ── Check frontend dist exists ────────────────────────────────────
if not exist "%~dp0frontend\dist\index.html" (
    echo ERROR: Frontend not built.
    echo   Run install.bat first to build the frontend.
    pause
    exit /b 1
)

:: ── Locate Python ──────────────────────────────────────────────────
set "PY_EXE="
if exist "%~dp0.venv\Scripts\python.exe" set "PY_EXE=%~dp0.venv\Scripts\python.exe"
if not defined PY_EXE if exist "%~dp0venv\Scripts\python.exe" set "PY_EXE=%~dp0venv\Scripts\python.exe"
if not defined PY_EXE where py >nul 2>&1 && set "PY_EXE=py -3"
if not defined PY_EXE where python >nul 2>&1 && set "PY_EXE=python"
if not defined PY_EXE (
    echo ERROR: Python not found.
    echo   Install Python 3.11+ from https://www.python.org/downloads/
    pause
    exit /b 1
)
echo [+] Python: %PY_EXE%

:: ── Check ffmpeg ──────────────────────────────────────────────────
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo [!] WARNING: ffmpeg not found in PATH. Video/audio features may fail.
)

:: ── Kill old process on port 9000 ─────────────────────────────────
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":9000" ^| findstr LISTENING') do (
    if not "%%p"=="" (
        echo [+] Cleaning old process PID %%p ...
        taskkill /f /t /pid %%p >nul 2>&1
    )
)
timeout /t 1 /nobreak >nul

:: ── Start backend (serves API + frontend) ─────────────────────────
cd /d %~dp0backend
timeout /t 1 /nobreak >nul
start "" http://localhost:9000
%PY_EXE% -m uvicorn main:app --host 0.0.0.0 --port 9000
