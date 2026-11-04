@echo off
title gpt2api - KleinAI
echo.
echo ========================================
echo   gpt2api (KleinAI) - Starting...
echo   Docker Compose stack
echo ========================================
echo.

:: ── Check Docker ──────────────────────────────────────────────────
where docker >nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker not found.
    echo   Install Docker Desktop from https://www.docker.com/products/docker-desktop/
    pause
    exit /b 1
)

docker info >nul 2>&1
if errorlevel 1 (
    echo [!] Docker Desktop is not running. Attempting to start...
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    echo Waiting for Docker to start...
    :wait_docker
    timeout /t 5 /nobreak >nul
    docker info >nul 2>&1
    if errorlevel 1 goto wait_docker
    echo [+] Docker is ready.
)

:: ── Check submodule ──────────────────────────────────────────────
if not exist "%~dp0gpt2api\deploy\docker-compose.server.yml" (
    echo ERROR: gpt2api submodule not found.
    echo   Run: git submodule update --init --recursive
    pause
    exit /b 1
)

:: ── Set compose project name ─────────────────────────────────────
set COMPOSE_PROJECT_NAME=gpt2api

:: ── Start containers ─────────────────────────────────────────────
echo Starting gpt2api containers...
echo   mysql, redis, flaresolverr, api, admin, openai, worker, user-web, admin-web
echo.

cd /d "%~dp0gpt2api\deploy"
docker compose -f docker-compose.server.yml up -d --build
if errorlevel 1 (
    echo.
    echo ERROR: docker compose up failed.
    echo   Check Docker Desktop for details.
    pause
    exit /b 1
)

:: ── Wait for services ────────────────────────────────────────────
echo.
echo Waiting for services to be ready...

set /a "attempts=0"
:wait_loop
set /a "attempts+=1"
if %attempts% gtr 60 (
    echo [!] Timeout waiting for services. Check Docker logs:
    echo     docker compose -f docker-compose.server.yml logs
    goto ready
)
timeout /t 5 /nobreak >nul

:: Check OpenAI endpoint
curl -s -o nul -w "%%{http_code}" http://127.0.0.1:17200/v1/models >"%temp%\gpt2api_check.txt" 2>nul
set /p http_code=<"%temp%\gpt2api_check.txt"
if "%http_code%"=="200" goto ready
if "%http_code%"=="401" goto ready
if "%http_code%"=="403" goto ready

:: Check admin endpoint
curl -s -o nul -w "%%{http_code}" http://127.0.0.1:17188/admin/api/v1/accounts >"%temp%\gpt2api_check.txt" 2>nul
set /p http_code=<"%temp%\gpt2api_check.txt"
if "%http_code%"=="200" goto ready
if "%http_code%"=="401" goto ready

echo [%attempts%/60] Still waiting...
goto wait_loop

:ready
echo.
echo ========================================
echo   gpt2api is ready!
echo.
echo   OpenAI API:    http://127.0.0.1:17200/v1
echo   User frontend: http://127.0.0.1:17080
echo   Admin panel:   http://127.0.0.1:17088
echo   Admin login:   admin / admin123
echo ========================================
echo.
pause
