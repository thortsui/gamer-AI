@echo off
rem Open the "what is it thinking" page. Starts the dashboard server if it is not running.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
powershell -NoProfile -Command "if (-not (Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*dashboard_server*' })) { Start-Process -FilePath python -ArgumentList '-u','dashboard_server.py','--port','8300' -WorkingDirectory '%~dp0' -WindowStyle Minimized; Start-Sleep -Seconds 3 }"
start "" "http://127.0.0.1:8300/think"
