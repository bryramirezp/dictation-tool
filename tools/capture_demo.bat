@echo off
rem Double-click this instead of typing the python command.
rem Records one dictation into docs/demo-data.json. Run it again anytime --
rem it always replaces the take with a new one.
setlocal
cd /d "%~dp0.."

py -3 tools\capture_demo.py

echo.
echo Press any key to close this window.
pause >nul
