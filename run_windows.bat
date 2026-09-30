@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul || (
  echo [ERROR] Python 3.11+ not found. Install from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  pause & exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  python -m venv .venv || (echo [ERROR] venv failed & pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo [ERROR] pip install failed & pause & exit /b 1)
)

if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo Created .env from .env.example - add HELIUS_API_KEY / TELEGRAM_BOT_TOKEN there if you have them.
)

".venv\Scripts\python.exe" src\main.py %*
if errorlevel 1 pause
