@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
echo Open http://127.0.0.1:8787 in this Windows machine's browser.
".venv\Scripts\python.exe" -m router.monitor
pause
