@echo off
setlocal
cd /d "%~dp0"
echo ===== JARVIS setup =====
echo.

rem --- Python 3.12 is required (faster-whisper/mediapipe don't support newer versions yet)
where py >nul 2>nul || goto :nopython
py -3.12 --version >nul 2>nul || goto :nopython

if not exist ".venv\Scripts\python.exe" (
    echo Creating Python environment...
    py -3.12 -m venv .venv || goto :fail
)

echo Installing packages - this can take several minutes...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
rem constraints.txt pins the exact versions known to work together; newer releases
rem of the speech/vision libraries can crash, so this also downgrades them if needed.
".venv\Scripts\python.exe" -m pip install -r requirements.txt -c constraints.txt || goto :fail

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo.
    echo Opening .env in Notepad: paste your API keys, then save and close it.
    notepad ".env"
)

echo.
echo ===== Setup complete =====
echo Start JARVIS by double-clicking run_jarvis.bat
echo Optional: double-click add_to_startup.bat to launch it automatically at login.
pause
exit /b 0

:nopython
echo Python 3.12 was not found.
echo Install it by running this command, then run setup.bat again:
echo     winget install Python.Python.3.12
echo Or download it from https://www.python.org/downloads/
pause
exit /b 1

:fail
echo.
echo Setup failed - see the error above.
pause
exit /b 1
