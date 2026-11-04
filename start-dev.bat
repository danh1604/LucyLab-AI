@echo off
echo Starting in DEV mode (hot reload)...
echo Backend: http://localhost:9000/api
echo Frontend: http://localhost:5700
echo.

:: ── Check config.json exists ──────────────────────────────────────
if not exist "%~dp0config.json" (
    echo ERROR: config.json not found.
    echo   Run install.bat first, then edit config.json with your settings.
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

:: ── Check Node.js ────────────────────────────────────────────────
where node >nul 2>&1
if errorlevel 1 (
    echo ERROR: Node.js not found. Install from https://nodejs.org/
    pause
    exit /b 1
)

:: ── Kill old processes on ports ───────────────────────────────────
for %%P in (9000 5700) do (
    for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%%P" ^| findstr LISTENING') do (
        if not "%%p"=="" (
            echo [+] Cleaning old process on port %%P - PID %%p ...
            taskkill /f /t /pid %%p >nul 2>&1
        )
    )
)
timeout /t 1 /nobreak >nul

:: ── Start backend and frontend ────────────────────────────────────
start "LucyLab Backend" cmd /k "cd /d %~dp0backend && %PY_EXE% -m uvicorn main:app --host 0.0.0.0 --port 9000 --reload"
timeout /t 2 /nobreak > nul
start "LucyLab Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"
timeout /t 3 /nobreak > nul
start http://localhost:5700
