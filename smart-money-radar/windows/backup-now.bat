@echo off
REM Sao luu database ngay lap tuc vao thu muc backups\
cd /d "%~dp0.."
docker compose -f docker-compose.windows.yml exec -T backup sh /backup.sh now
echo.
dir /b /o-d backups\*.dump
pause
