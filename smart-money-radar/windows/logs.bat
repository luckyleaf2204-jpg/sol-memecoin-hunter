@echo off
REM 200 dong log gan nhat cua worker va api (API key da duoc che trong log)
cd /d "%~dp0.."
docker compose -f docker-compose.windows.yml logs --tail 200 worker api
pause
