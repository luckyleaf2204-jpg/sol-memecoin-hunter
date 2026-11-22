@echo off
REM Dung he thong (du lieu van giu nguyen). Sau khi dung, Docker Desktop se KHONG tu bat lai
REM cho den khi chay start.bat.
cd /d "%~dp0.."
docker compose -f docker-compose.windows.yml stop
pause
