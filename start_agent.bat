@echo off
rem gamer AI: start the autonomous Roblox play agent (16 hours; game name defaults to the one in play_agent.py)
rem Stop: press F12, or throw the mouse into the top-left corner, or create a file named stop.txt in this folder.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
start "gamer-AI agent" /min cmd /k python -u play_agent.py --hours 16
