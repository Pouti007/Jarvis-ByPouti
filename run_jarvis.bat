@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo JARVIS isn't installed yet - double-click setup.bat first.
    pause
    exit /b 1
)
echo Starting JARVIS... the first launch downloads the speech models (~500 MB),
echo so it may take a few minutes before it greets you.
echo.
rem -X faulthandler prints a stack trace if a native library crashes, instead of exiting silently.
".venv\Scripts\python.exe" -X faulthandler main.py
set EXITCODE=%ERRORLEVEL%
echo.
if not "%EXITCODE%"=="0" echo JARVIS stopped with exit code %EXITCODE%. Send a screenshot of this window for help.
pause
