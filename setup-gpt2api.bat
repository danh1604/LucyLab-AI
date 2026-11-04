@echo off
title gpt2api Setup
echo.
echo ========================================
echo   gpt2api - First-time Setup
echo ========================================
echo.
echo This script will:
echo   1. Start gpt2api Docker containers
echo   2. Create a GPT account (needs your OpenAI OAuth tokens)
echo   3. Create a local API key for LucyLab
echo.
echo Prerequisites:
echo   - Docker Desktop installed and running
echo   - OpenAI/ChatGPT OAuth tokens (access_token or refresh_token)
echo.
pause

:: ── Check Docker ──────────────────────────────────────────────────
where docker >nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker not found. Install Docker Desktop first.
    pause
    exit /b 1
)

docker info >nul 2>&1
if errorlevel 1 (
    echo [!] Docker Desktop is not running. Starting...
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    :wait_docker_setup
    timeout /t 5 /nobreak >nul
    docker info >nul 2>&1
    if errorlevel 1 goto wait_docker_setup
)

:: ── Start gpt2api ────────────────────────────────────────────────
echo.
echo [1/3] Starting gpt2api containers...
set COMPOSE_PROJECT_NAME=gpt2api
cd /d "%~dp0gpt2api\deploy"
docker compose -f docker-compose.server.yml up -d --build
if errorlevel 1 (
    echo ERROR: Failed to start containers.
    pause
    exit /b 1
)

:: ── Wait for services ────────────────────────────────────────────
echo [2/3] Waiting for services to be ready (this may take 1-2 minutes)...
set /a "wait_count=0"
:wait_setup
set /a "wait_count+=1"
if %wait_count% gtr 60 (
    echo [!] Services not ready after 5 minutes. Check Docker logs.
    pause
    exit /b 1
)
timeout /t 5 /nobreak >nul

:: Check admin endpoint
curl -s -o nul -w "%%{http_code}" http://127.0.0.1:17188/admin/api/v1/accounts >"%temp%\gpt2api_setup.txt" 2>nul
set /p admin_code=<"%temp%\gpt2api_setup.txt"
if not "%admin_code%"=="200" if not "%admin_code%"=="401" (
    echo [%wait_count%/60] Waiting for admin API...
    goto wait_setup
)

:: Check openai endpoint
curl -s -o nul -w "%%{http_code}" http://127.0.0.1:17200/v1/models >"%temp%\gpt2api_setup.txt" 2>nul
set /p openai_code=<"%temp%\gpt2api_setup.txt"
if not "%openai_code%"=="200" if not "%openai_code%"=="401" if not "%openai_code%"=="403" (
    echo [%wait_count%/60] Waiting for OpenAI API...
    goto wait_setup
)

echo [+] Services are ready.

:: ── Run setup via PowerShell ─────────────────────────────────────
echo.
echo [3/3] Running account setup...
echo.
echo You will need your OpenAI/ChatGPT OAuth tokens.
echo To get them:
echo   1. Go to https://chatgpt.com and log in
echo   2. Open DevTools (F12) → Application → Cookies
echo   3. Find __Secure-next-auth.session-token (this is your session_token/id_token)
echo   4. Or use the Network tab to find access_token and refresh_token
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-gpt2api.ps1"
if errorlevel 1 (
    echo.
    echo Setup failed. You can retry later by running this script again.
    pause
    exit /b 1
)

echo.
echo ========================================
echo   Setup complete!
echo.
echo   Next steps:
echo   1. Open LucyLab Settings
echo   2. Go to Image API tab
echo   3. Set provider to "gpt2api"
echo   4. The API key is auto-detected from gpt2api/.local/
echo ========================================
pause
