@echo off
echo.
echo ========================================
echo   LucyLab Story Studio - Setup
echo ========================================
echo.

:: ── Locate Python ──────────────────────────────────────────────────
set "PY_EXE="
if exist "%~dp0.venv\Scripts\python.exe" set "PY_EXE=%~dp0.venv\Scripts\python.exe"
if not defined PY_EXE if exist "%~dp0venv\Scripts\python.exe" set "PY_EXE=%~dp0venv\Scripts\python.exe"
if not defined PY_EXE where py >nul 2>&1 && set "PY_EXE=py -3"
if not defined PY_EXE where python >nul 2>&1 && set "PY_EXE=python"
if not defined PY_EXE (
    echo ERROR: Python not found.
    echo   Install Python 3.11+ from https://www.python.org/downloads/
    echo   Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)
echo [+] Python: %PY_EXE%

:: ── Locate Node.js ────────────────────────────────────────────────
where node >nul 2>&1
if errorlevel 1 (
    echo ERROR: Node.js not found.
    echo   Install Node.js 18+ from https://nodejs.org/
    pause
    exit /b 1
)
where npm >nul 2>&1
if errorlevel 1 (
    echo ERROR: npm not found. Please reinstall Node.js.
    pause
    exit /b 1
)
echo [+] Node.js: 
node --version

:: ── Check ffmpeg ──────────────────────────────────────────────────
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo [!] WARNING: ffmpeg not found in PATH.
    echo     Video/audio features will not work without ffmpeg.
    echo     Install from https://ffmpeg.org/download.html or run:
    echo       winget install ffmpeg
    echo.
)

:: ── Init git submodules (gpt2api) ─────────────────────────────────
if exist "%~dp0.git" (
    echo.
    echo Initializing git submodules...
    cd /d %~dp0
    git submodule update --init --recursive
    if errorlevel 1 (
        echo [!] WARNING: git submodule init failed.
        echo     gpt2api image generation may not work.
        echo     You can retry manually: git submodule update --init --recursive
    ) else (
        echo [+] Submodules initialized.
    )
)

:: ── Copy config template ──────────────────────────────────────────
if not exist "%~dp0config.json" (
    if exist "%~dp0config.example.json" (
        copy "%~dp0config.example.json" "%~dp0config.json" >nul
        echo [+] Created config.json from template.
        echo     Edit config.json to add your API keys and settings.
    )
)

:: ── Install Python dependencies ───────────────────────────────────
echo.
echo [1/3] Installing Python dependencies...
cd /d %~dp0backend
%PY_EXE% -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo ERROR: pip install failed
    pause & exit /b 1
)

:: ── Install frontend dependencies ─────────────────────────────────
echo.
echo [2/3] Installing frontend dependencies...
cd /d %~dp0frontend
call npm install
if errorlevel 1 (
    echo ERROR: npm install failed
    pause & exit /b 1
)

:: ── Build frontend ────────────────────────────────────────────────
echo.
echo [3/3] Building frontend...
call npm run build
if errorlevel 1 (
    echo ERROR: npm build failed
    pause & exit /b 1
)

echo.
echo ========================================
echo   Setup complete!
echo.
echo   1. Edit config.json to add your API keys
echo   2. Run start.bat to launch the app
echo   3. Open http://localhost:9000
echo ========================================
pause
