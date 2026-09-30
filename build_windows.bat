@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul || (echo [ERROR] Python 3.11+ not found in PATH & pause & exit /b 1)

if not exist ".venv\Scripts\python.exe" python -m venv .venv
set PY=.venv\Scripts\python.exe

%PY% -m pip install --upgrade pip
%PY% -m pip install -r requirements.txt pyinstaller || (echo [ERROR] pip install failed & pause & exit /b 1)

echo Running tests...
%PY% -m pytest -q || (echo [ERROR] tests failed - not building & pause & exit /b 1)

echo Building SOL_Memecoin_Hunter.exe ...
%PY% -m PyInstaller --noconfirm --clean SOL_Memecoin_Hunter.spec || (echo [ERROR] build failed & pause & exit /b 1)

copy /y ".env.example" "dist\.env.example" >nul
if exist ".env" copy /y ".env" "dist\.env" >nul

echo.
echo Done:  dist\SOL_Memecoin_Hunter.exe
echo Put your .env next to the exe. Data (SQLite DB, settings) is stored in dist\data\
pause
