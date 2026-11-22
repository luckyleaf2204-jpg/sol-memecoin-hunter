@echo off
REM Mo dashboard cho cac thiet bi Tailscale cua ban (iPhone, laptop) qua HTTPS rieng tu.
REM Can: da cai Tailscale tren may nay va da dang nhap. Dashboard van chi nghe 127.0.0.1.
set TS="C:\Program Files\Tailscale\tailscale.exe"
if not exist %TS% (
  echo Chua cai Tailscale. Tai tai https://tailscale.com/download/windows roi chay lai.
  pause
  exit /b 1
)
set PORT=8080
cd /d "%~dp0.."
for /f "tokens=1,* delims==" %%a in ('findstr /b "LOCAL_PORT=" .env') do set PORT=%%b
%TS% serve --bg %PORT%
echo.
%TS% serve status
echo.
echo Mo dia chi https://... o tren bang Safari tren iPhone (iPhone cung phai dang nhap Tailscale).
echo Tat chia se: chay   %TS% serve reset
pause
