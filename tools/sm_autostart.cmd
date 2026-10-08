@echo off
rem Smart-money recorder watchdog (docs/smart_money_audit_2026-10-08.md). Copied to the Startup folder; delete it there to stop autostart.
cd /d "D:\code\sol_memecoin_hunter"
start "" "C:\Users\KuBee\AppData\Local\Programs\Python\Python312\pythonw.exe" "D:\code\sol_memecoin_hunter\tools\sm_watchdog.py"
