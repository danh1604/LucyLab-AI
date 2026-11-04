@echo off
echo Stopping gpt2api containers...
set COMPOSE_PROJECT_NAME=gpt2api
cd /d "%~dp0gpt2api\deploy"
docker compose -f docker-compose.server.yml down
echo Done.
pause
