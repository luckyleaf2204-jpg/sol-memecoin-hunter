@echo off
REM Khoi phuc database tu mot file trong thu muc backups\
cd /d "%~dp0.."
echo Cac ban sao luu (moi nhat o tren):
dir /b /o-d backups\*.dump
echo.
set /p FILE=Nhap ten file can khoi phuc (vi du radar-20261122-050000.dump): 
if "%FILE%"=="" exit /b 1
if not exist "backups\%FILE%" (
  echo [LOI] Khong tim thay backups\%FILE%
  pause
  exit /b 1
)
echo CANH BAO: du lieu hien tai se bi THAY THE boi %FILE%.
set /p OK=Go YES de tiep tuc: 
if /i not "%OK%"=="YES" exit /b 1
docker compose -f docker-compose.windows.yml stop worker api frontend caddy
docker compose -f docker-compose.windows.yml exec -T backup sh /restore.sh "%FILE%"
docker compose -f docker-compose.windows.yml up -d
echo Xong. Mo lai dashboard.
pause
