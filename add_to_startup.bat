@echo off
rem Creates a Startup-folder shortcut so JARVIS launches minimized to the tray at login.
rem To undo: delete JARVIS.lnk from  shell:startup  (Win+R, type shell:startup).
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Run setup.bat first.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=(Get-Location).Path; $s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\JARVIS.lnk'); $s.TargetPath=$p+'\.venv\Scripts\pythonw.exe'; $s.Arguments='\"'+$p+'\main.py\" --minimized'; $s.WorkingDirectory=$p; $s.IconLocation=$p+'\gui\assets\icon.ico'; $s.Save()"
echo JARVIS will now start minimized in the system tray when you log in.
pause
