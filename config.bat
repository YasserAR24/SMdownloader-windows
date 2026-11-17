@echo off
cd /d "%~dp0"
set "VENV=%~dp0.SMdownloadervenv"

echo Welcome to SMdownloader setup.
echo.

set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python --version >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo Python is not installed.
    echo Run: winget install Python.Python.3.13
    echo Then CLOSE this window and run config.bat again.
    pause
    exit /b 1
)
%PY% --version

where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo FFmpeg is not installed.
    echo Run: winget install Gyan.FFmpeg
    echo Then CLOSE this window and run config.bat again.
    pause
    exit /b 1
)
echo FFmpeg found.

where deno >nul 2>&1
if errorlevel 1 (
    echo.
    echo WARNING: Deno is not installed. YouTube downloads may fail or miss formats.
    echo Run: winget install DenoLand.Deno
    echo Then CLOSE this window and run config.bat again.
    echo.
)

if not exist "%VENV%\Scripts\python.exe" (
    echo Creating the virtual environment...
    %PY% -m venv "%VENV%"
    if errorlevel 1 goto fail
)

echo Installing packages...
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto fail
"%VENV%\Scripts\python.exe" -m pip install --upgrade "yt-dlp[default]" flask
if errorlevel 1 goto fail

echo.
echo ---- Done ----
echo Now double-click runSMdownloader.bat
pause
exit /b 0

:fail
echo.
echo Something failed. Check the error above and try again.
pause
exit /b 1
