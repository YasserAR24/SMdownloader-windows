@echo off
cd /d "%~dp0"
set "VPY=%~dp0.SMdownloadervenv\Scripts\python.exe"

if not exist "%VPY%" (
    echo Setup was not done. Run config.bat first.
    pause
    exit /b 1
)

netstat -ano | findstr /R /C:":5000 .*LISTENING" >nul
if not errorlevel 1 (
    echo Port 5000 is already in use. SMdownloader may already be running.
    start "" "http://127.0.0.1:5000"
    pause
    exit /b 1
)

start "" /min cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:5000"

cd app-code
"%VPY%" SMdownloader.py

echo.
echo SMdownloader stopped.
pause
