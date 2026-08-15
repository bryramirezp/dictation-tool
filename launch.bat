@echo off
rem Starts Dictation Tool with no console window.
rem
rem To see errors while troubleshooting, run this instead:
rem     py -3 dictation_tool.py
rem
rem If you keep the dependencies in one specific Python, pin it once:
rem     setx DICTATION_PYTHON "C:\Path\To\pythonw.exe"
setlocal

if defined DICTATION_PYTHON (
    start "" /B "%DICTATION_PYTHON%" "%~dp0dictation_tool.py"
    exit /b
)

rem The py launcher ships with every python.org install and picks the newest
rem interpreter itself, so this works regardless of what is on PATH.
if exist "%SystemRoot%\pyw.exe" (
    start "" /B "%SystemRoot%\pyw.exe" -3 "%~dp0dictation_tool.py"
    exit /b
)

where pythonw >nul 2>&1
if not errorlevel 1 (
    start "" /B pythonw "%~dp0dictation_tool.py"
    exit /b
)

echo Python 3.10 or newer was not found.
echo.
echo Install it from https://www.python.org/downloads/
echo During setup, tick "Add python.exe to PATH".
echo.
pause
exit /b 1
