@echo off

echo Welcome to SMdownloader.
echo.

where winget >nul 2>&1

if errorlevel 1 (
    echo Winget was not found on your computer.
    echo Please install or update "App Installer" from the Microsoft Store.
    echo Then run this file again.
    echo.
    pause
    exit /b
)


where python >nul 2>&1

if errorlevel 1 (
    echo Python is not installed.
    echo Installing Python...
    winget install --id Python.Python.3.13
    echo.
    echo Python installation is finished.
    echo.
) else (
    echo Python is already installed.
    echo Checking for a new version...
    winget upgrade --id Python.Python.3.13
    echo.
)

where ffmpeg >nul 2>&1

if errorlevel 1 (
    echo FFmpeg is not installed.
    echo Installing FFmpeg...
    winget install Gyan.FFmpeg
    echo.
    echo FFmpeg installation is finished.
    echo.
) else (
    echo FFmpeg is already installed.
    echo Checking for a new version...
    winget upgrade Gyan.FFmpeg
)

echo.
echo ----Done----
echo If you installed something new, close this window and open a new one.
pause
exit /b
