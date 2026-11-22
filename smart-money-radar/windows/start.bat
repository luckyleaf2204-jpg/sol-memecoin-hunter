@echo off
REM Khoi dong Smart Money Radar (lan dau se build image, mat 5-15 phut)
cd /d "%~dp0.."
docker info >nul 2>&1
if errorlevel 1 (
  echo [LOI] Docker Desktop chua chay. Mo Docker Desktop, doi bieu tuong ca voi xanh roi chay lai.
  pause
  exit /b 1
)
if not exist ".env" (
  echo [LOI] Chua co file .env. Chay windows\setup.bat truoc.
  pause
  exit /b 1
)
set PORT=8080
for /f "tokens=1,* delims==" %%a in ('findstr /b "LOCAL_PORT=" .env') do set PORT=%%b
set PROFILE=
for /f "tokens=1,* delims==" %%a in ('findstr /b "CLOUDFLARE_TUNNEL_TOKEN=" .env') do if not "%%b"=="" set PROFILE=--profile tunnel
if defined PROFILE echo Co CLOUDFLARE_TUNNEL_TOKEN: bat ca Cloudflare Tunnel.
echo Dang khoi dong (lan dau se build image)...
docker compose -f docker-compose.windows.yml %PROFILE% up -d --build
if errorlevel 1 (
  echo [LOI] Khoi dong that bai. Chay windows\logs.bat va gui noi dung loi.
  pause
  exit /b 1
)
echo Doi he thong san sang...
for /l %%i in (1,1,60) do (
  curl -s -o nul -w "%%{http_code}" http://localhost:%PORT%/api/health | findstr 200 >nul && goto ready
  timeout /t 5 /nobreak >nul
)
echo [CANH BAO] Chua thay API tra loi sau 5 phut. Chay windows\status.bat de kiem tra.
pause
exit /b 1
:ready
echo.
echo Smart Money Radar dang chay: http://localhost:%PORT%
start "" http://localhost:%PORT%
pause
