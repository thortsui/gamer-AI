@echo off
rem 一鍵啟動 gamer AI。用法：開始玩.bat [遊戲名] [玩到 HH:mm]
rem   開始玩.bat                → 新遊戲，玩到 21:00
rem   開始玩.bat 新遊戲 18:30
cd /d "%~dp0"
set G=%~1
set T=%~2
if "%G%"=="" set G=新遊戲
if "%T%"=="" set T=21:00
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0開始玩.ps1" -Game "%G%" -Until "%T%"
pause
