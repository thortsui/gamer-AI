@echo off
rem gamer AI live dashboard (opens http://localhost:8300)
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
start "gamer-AI dashboard" /min cmd /k python -u dashboard_server.py --port 8300
timeout /t 2 >nul
start http://localhost:8300
