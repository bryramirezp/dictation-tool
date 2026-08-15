@echo off
set "PYTHONW=C:\Users\Bryan\AppData\Local\Programs\Python\Python311\pythonw.exe"
if not exist "%PYTHONW%" set "PYTHONW=pythonw.exe"
start "" /B "%PYTHONW%" "%~dp0dictation_tool.py"
