@echo off
cd /d "%~dp0.."
set PORT=8080
for /f "tokens=1,* delims==" %%a in ('findstr /b "LOCAL_PORT=" .env') do set PORT=%%b
docker compose -f docker-compose.windows.yml --profile tunnel ps
echo.
echo Kiem tra API: 
curl -s http://localhost:%PORT%/api/health
echo.
pause
