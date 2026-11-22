@echo off
REM Cap nhat phien ban moi (can Git) va build lai. Du lieu duoc giu nguyen.
cd /d "%~dp0.."
where git >nul 2>&1
if errorlevel 1 (
  echo Khong co Git: tai ban ZIP moi tu GitHub, giai nen DE LEN thu muc cu ^(giu file .env va thu muc backups^).
) else (
  git pull
)
call "%~dp0start.bat"
